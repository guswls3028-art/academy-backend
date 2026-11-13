from unittest.mock import patch
from urllib.parse import urlencode

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models import StudentReportedScore


User = get_user_model()
TERM = {"academic_year": 2026, "semester": 1, "exam_round": "first"}
MATH = {"subject": "수학", "score": "89.25", "max_score": 100}
KOREAN = {"subject": "국어", "score": 95, "max_score": 100}


class SchoolReportCollectionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="qa-school-reports", name="학교 성적 QA")
        self.other = Tenant.objects.create(code="qa-other-reports", name="다른 학원 QA")
        self.teacher = self.user(self.tenant, "teacher", "teacher")
        self.outside_teacher = self.user(self.other, "outside-teacher", "teacher")
        self.student = self.make_student(self.tenant, "own", "가학생")
        self.second = self.make_student(self.tenant, "second", "나학생")
        self.disabled = self.make_student(self.tenant, "disabled", "다학생")
        self.disabled.user.is_active = False
        self.disabled.user.save(update_fields=["is_active"])
        self.outside = self.make_student(self.other, "outside", "외부학생")

    def user(self, tenant, key, role):
        user = User.objects.create_user(username=f"qa-school-{key}", tenant=tenant)
        TenantMembership.ensure_active(tenant=tenant, user=user, role=role)
        return user

    def make_student(self, tenant, key, name):
        student = apps.get_model("students", "Student").objects.create(
            tenant=tenant, user=self.user(tenant, key, "student"), name=name,
            ps_number=f"QA{key}", omr_code={"own": "11111111", "second": "22222222",
                                            "disabled": "33333333", "outside": "44444444"}[key],
            school_type="HIGH", high_school="가고", grade=2, phone="01000001234",
        )
        return student

    def call(self, route, user, *, method="get", query=None, body=None, kwargs=None, tenant=None, support=False):
        path = reverse(route, kwargs=kwargs)
        path_with_query = f"{path}?{urlencode(query)}" if query else path
        request = getattr(self.factory, method)(path_with_query, body, format="json")
        request.tenant = tenant or self.tenant
        if support:
            request.student_support_session = object()
        force_authenticate(request, user=user)
        matched = resolve(path)
        return matched.func(request, **matched.kwargs)

    def get_own(self, student=None, query=None):
        return self.call("school-report-me", (student or self.student).user, query=query or TERM)

    def put(self, subjects, query=None):
        return self.call(
            "school-report-me", self.student.user, method="put", query=query or TERM, body={"subjects": subjects},
        )

    def staff_detail(self, student=None, query=None):
        return self.call(
            "school-report-detail", self.teacher, query=query or TERM,
            kwargs={"student_id": (student or self.student).id},
        )

    def request_scores(self, *students, user=None, term=None):
        return self.call(
            "school-report-requests", user or self.teacher, method="post",
            body={**(term or TERM), "student_ids": [student.id for student in students]},
        )

    def evidence_score(self):
        evidence = apps.get_model("inventory", "InventoryFile").objects.create(
            tenant=self.tenant, scope="student", student_ps=self.student.ps_number,
            display_name="성적표.jpg", original_name="score.jpg", r2_key="qa/school-score.jpg",
            content_type="image/jpeg",
        )
        return StudentReportedScore.objects.create(
            tenant=self.tenant, student=self.student, submitted_by=self.student.user,
            evidence_file=evidence, source=StudentReportedScore.Source.SCHOOL_EXAM,
            subject="수학", score=80, max_score=100, status=StudentReportedScore.Status.VERIFIED,
            **TERM,
        )

    def test_student_save_reload_staff_detail_and_legacy_evidence_preserved(self):
        legacy = self.evidence_score()
        saved = self.put([{**MATH, "grade_rank": 3, "grade_scale": "five"}, KOREAN])
        self.assertEqual(saved.status_code, 200, saved.data)
        first = saved.data["direct_scores"][0]
        self.assertEqual(first["score"], 89.25)
        self.assertEqual(first["status"], "pending")
        self.assertTrue(first["direct_entry"])
        self.assertEqual(self.get_own().data, saved.data)
        self.assertEqual(self.staff_detail().data, saved.data)
        self.assertEqual(saved.data["school"], "가고")
        self.assertEqual(saved.data["grade"], 2)
        self.assertEqual(self.get_own(query={**TERM, "semester": 2}).data["status"], "missing")

        updated = self.put([{**MATH, "score": 90}])
        row = updated.data["direct_scores"][0]
        self.assertEqual((row["id"], row["score"], row["grade_rank"]), (first["id"], 90, 3))
        self.assertEqual(len(updated.data["scores"]), 1)
        self.assertEqual(self.put([{**MATH, "score": 90}]).data, updated.data)
        self.assertEqual(
            StudentReportedScore.objects.filter(direct_entry=True, status="self_removed").count(), 1,
        )
        legacy.refresh_from_db()
        self.assertEqual((legacy.status, legacy.score), ("verified", 80))
        self.assertIsNotNone(legacy.evidence_file_id)
        self.assertEqual(
            self.call(
                "admin-student-reported-score-review", self.teacher, method="patch", body={"action": "verify"},
                kwargs={"score_id": first["id"]},
            ).status_code,
            409,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            StudentReportedScore.objects.filter(pk=first["id"]).update(status="verified")

    def test_invalid_entries_rollback_and_never_use_target_override(self):
        self.assertEqual(self.put([MATH]).status_code, 200)
        for invalid in ([{**MATH, "score": 91}, {**KOREAN, "score": 120}], [MATH, MATH], [], [{**MATH, "score": "1.005"}]):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.put(invalid).status_code, 400)
        self.assertEqual(self.get_own().data["scores"][0]["score"], 89.25)
        self.assertEqual(self.call(
            "school-report-me", self.student.user, method="put", query=TERM, body={"subjects": [KOREAN]},
            support=True,
        ).status_code, 403)
        self.assertEqual(self.get_own().data["scores"][0]["subject"], "수학")
        self.assertEqual(self.put([MATH], query={**TERM, "exam_round": "performance"}).status_code, 400)
        self.assertEqual(self.get_own(self.outside).status_code, 403)
        self.assertEqual(self.staff_detail(self.outside).status_code, 404)
        self.assertEqual(self.get_own(self.disabled).status_code, 403)
        self.assertEqual(
            self.call("school-report-roster", self.student.user, query=TERM).status_code, 403,
        )
        self.assertEqual(
            self.call("school-report-roster", self.outside_teacher, query=TERM).status_code, 403,
        )

    def test_roster_status_count_search_lecture_and_recipients(self):
        self.evidence_score()
        lecture = apps.get_model("lectures", "Lecture").objects.create(
            tenant=self.tenant, title="QA 강의", name="QA 강의", subject="수학",
        )
        apps.get_model("enrollment", "Enrollment").objects.create(
            tenant=self.tenant, lecture=lecture, student=self.student, status="ACTIVE",
        )
        result = self.call("school-report-roster", self.teacher, query={**TERM, "status": "missing", "page_size": 1})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["count"], 2)
        self.assertEqual(
            [result.data[key] for key in ("total_count", "submitted_count", "missing_count")], [3, 1, 2],
        )
        self.assertEqual(result.data["results"][0]["student_id"], self.second.id)
        self.assertTrue(result.data["results"][0]["can_request"])
        search = self.call("school-report-roster", self.teacher, query={**TERM, "search": "다학생"})
        self.assertEqual(search.data["count"], 1)
        self.assertFalse(search.data["results"][0]["can_request"])
        filtered = self.call(
            "school-report-roster", self.teacher, query={**TERM, "lecture_id": lecture.id},
        )
        self.assertEqual(
            [filtered.data[key] for key in ("total_count", "submitted_count", "missing_count")], [1, 1, 0],
        )
        self.assertEqual(filtered.data["results"][0]["score_count"], 1)

    def test_manual_inbox_request_is_missing_only_idempotent_and_never_enqueued(self):
        self.evidence_score()
        with patch("apps.domains.messaging.services.queue_service.enqueue_alimtalk") as enqueue:
            rejected = self.request_scores(self.second, self.outside)
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(self.request_scores(self.second, user=self.student.user).status_code, 403)
            created = self.request_scores(self.student, self.second, self.second, self.disabled)
            self.assertEqual(created.status_code, 200, created.data)
            self.assertEqual(created.data, {"requested": 1, "skipped": 2})
            self.assertEqual(self.request_scores(self.second).data, {"requested": 0, "skipped": 1})
            enqueue.assert_not_called()
        notification = apps.get_model("community", "CommunityNotification").objects.get()
        self.assertEqual(notification.recipient_id, self.second.user_id)
        self.assertEqual(notification.payload, TERM)
        self.assertEqual(self.get_own(self.second).data["requested_at"], notification.created_at.isoformat())
        self.assertEqual(self.staff_detail(self.second).data["requested_at"], notification.created_at.isoformat())
        self.assertIsNone(self.get_own(self.second, query={**TERM, "exam_round": "second"}).data["requested_at"])
        inbox = self.call(
            "community-notification-list", self.second.user, query={"kind": "school_report_request"},
        )
        self.assertEqual(inbox.data["count"], 1)
        self.assertEqual(inbox.data["results"][0]["payload"], TERM)
        first_inbox = self.call(
            "community-notification-list", self.student.user, query={"kind": "school_report_request"},
        )
        self.assertEqual(first_inbox.data["count"], 0)
        self.assertEqual(self.call(
            "community-notification-list", self.second.user, query={"kind": "wrong"},
        ).status_code, 400)

    def test_legacy_insert_without_direct_column_is_visible_to_new_reader(self):
        legacy = self.evidence_score()
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO results_student_reported_score (
                    tenant_id, student_id, submitted_by_id, evidence_file_id, source, academic_year,
                    semester, exam_round, exam_name, subject, score, max_score, status, review_note,
                    grade_scale, achievement_level, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [
                    self.tenant.id, self.student.id, self.student.user_id, legacy.evidence_file_id,
                    "school_exam", 2026, 1, "first", "", "영어", 88, 100, "pending", "", "", "",
                    timezone.now(), timezone.now(),
                ],
            )
            cursor.execute(
                "SELECT subject, status FROM results_student_reported_score WHERE student_id = %s AND subject = %s",
                [self.student.id, "영어"],
            )
            self.assertEqual(cursor.fetchone(), ("영어", "pending"))
        result = self.staff_detail()
        self.assertEqual(result.status_code, 200)
        english = next(row for row in result.data["scores"] if row["subject"] == "영어")
        self.assertFalse(english["direct_entry"])
        self.assertEqual(english["evidence_file_id"], legacy.evidence_file_id)

    def preview_alimtalk(self, *, user=None, students=None, send_to="student", context=None):
        return self.call(
            "manual-notification-preview", user or self.teacher, method="post",
            body={
                "trigger": "school_report_request", "send_to": send_to, "context": context,
                "context_source": {
                    "type": "school_report_request", **TERM,
                    "student_ids": [row.id for row in (students or [self.student])],
                },
            },
        )

    def confirm_alimtalk(self, token, *, user=None):
        return self.call(
            "manual-notification-confirm", user or self.teacher, method="post", body={"preview_token": token},
        )

    @override_settings(
        SOLAPI_KAKAO_PF_ID="TEST-PF", SOLAPI_API_KEY="test-key",
        SOLAPI_API_SECRET="test-secret", SOLAPI_SENDER="01000000000",
    )
    def test_kakao_exact_approval_and_manual_confirmation_without_external_dispatch(self):
        settings_context = override_settings(OWNER_TENANT_ID=self.tenant.id)
        with settings_context, patch("apps.domains.messaging.scheduled.process_due_notifications") as dispatch:
            self.tenant.domains.update_or_create(is_primary=True, defaults={"host": "school-qa.example.test"})
            result = self.call("school-report-roster", self.teacher, query=TERM)
            self.assertFalse(result.data["alimtalk_available"])
            self.assertEqual(self.preview_alimtalk().status_code, 409)
            template = apps.get_model("messaging", "MessageTemplate").objects.create(
                tenant=self.tenant, name="QA school submission", solapi_status="PENDING",
                solapi_template_id="QA-DEDICATED-SCHOOL-REPORT",
                body="#{학생이름}님, #{시험명} 학교 성적을 제출해 주세요. #{사이트링크}",
            )
            config = apps.get_model("messaging", "AutoSendConfig").objects.create(
                tenant=self.tenant, trigger="school_report_request", template=template, enabled=False,
            )
            self.assertEqual(self.preview_alimtalk().status_code, 409)
            template.solapi_status = "APPROVED"
            template.save(update_fields=["solapi_status"])
            owner = self.user(self.tenant, "owner", "owner")
            self.call(
                "messaging-auto-send", owner, method="patch",
                body={"configs": [{"trigger": "school_report_request", "enabled": True}]},
            )
            config.refresh_from_db()
            self.assertFalse(config.enabled)
            with override_settings(SOLAPI_SENDER=""):
                self.assertFalse(self.call("school-report-roster", self.teacher, query=TERM).data["alimtalk_available"])
                self.assertEqual(self.preview_alimtalk().status_code, 409)
            self.assertEqual(self.preview_alimtalk(send_to="parent").status_code, 400)
            self.assertEqual(self.preview_alimtalk(students=[self.outside]).status_code, 400)
            self.assertEqual(self.preview_alimtalk(context={"시험명": "잘못된 회차"}).status_code, 400)
            self.assertEqual(self.request_scores(self.student).data["requested"], 1)
            roster = self.call("school-report-roster", self.teacher, query=TERM)
            self.assertTrue(roster.data["alimtalk_available"])
            self.assertFalse(roster.data["results"][0]["can_request"])
            self.assertTrue(roster.data["results"][0]["can_request_alimtalk"])
            preview = self.preview_alimtalk()
            self.assertEqual(preview.status_code, 200, preview.data)
            recipient = preview.data["recipients"][0]
            self.assertIn(
                "https://school-qa.example.test/student/submit/school-report?academic_year=2026&semester=1&exam_round=first",
                recipient["message_body"],
            )
            self.assertNotIn("phone_raw", recipient)
            self.assertNotIn("alimtalk_replacements", recipient)
            outboxes = apps.get_model("messaging", "ScheduledNotification").objects.filter(
                trigger="school_report_request",
            )
            self.assertEqual(outboxes.count(), 0)
            other_staff = self.user(self.tenant, "second-teacher", "teacher")
            self.assertEqual(self.confirm_alimtalk(preview.data["preview_token"], user=other_staff).status_code, 403)
            stale = self.preview_alimtalk(students=[self.second])
            self.assertEqual(stale.status_code, 200)
            self.call("school-report-me", self.second.user, method="put", query=TERM, body={"subjects": [MATH]})
            self.assertEqual(self.confirm_alimtalk(stale.data["preview_token"]).status_code, 409)
            template.solapi_status = "PENDING"
            template.save(update_fields=["solapi_status"])
            self.assertEqual(self.confirm_alimtalk(preview.data["preview_token"]).status_code, 409)
            dispatch.assert_not_called()
            template.solapi_status = "APPROVED"
            template.save(update_fields=["solapi_status"])
            apps.get_model("messaging", "MessagingObserver").objects.create(
                tenant=self.tenant, user=self.user(self.tenant, "observer", "admin"),
            )
            observer = User.objects.get(username="qa-school-observer")
            observer.phone = "01000005678"
            observer.save(update_fields=["phone"])
            sent = self.confirm_alimtalk(preview.data["preview_token"])
            self.assertEqual(sent.status_code, 200, sent.data)
            self.assertEqual(sent.data["pending_count"], 1)
            self.assertEqual(self.confirm_alimtalk(preview.data["preview_token"]).data, sent.data)
            dispatch.assert_called_once()
            self.assertEqual(outboxes.count(), 1)
            self.assertEqual(outboxes.get().payload["target_type"], "student")
            self.assertEqual(outboxes.get().origin_id, "2026:1:first")
            config.refresh_from_db()
            self.assertFalse(config.enabled)
