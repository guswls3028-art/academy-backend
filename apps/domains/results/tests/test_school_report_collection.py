from unittest.mock import patch
from urllib.parse import urlencode

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import resolve, reverse
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
            school_type="HIGH", high_school="가고", grade=2,
        )
        return student

    def call(self, route, user, *, method="get", query=None, body=None, kwargs=None, tenant=None):
        path = reverse(route, kwargs=kwargs)
        path_with_query = f"{path}?{urlencode(query)}" if query else path
        request = getattr(self.factory, method)(path_with_query, body, format="json")
        request.tenant = tenant or self.tenant
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
