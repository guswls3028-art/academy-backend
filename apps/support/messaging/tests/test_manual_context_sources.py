from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.clinic.models import Session, SessionParticipant
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, NotificationPreviewToken
from apps.domains.messaging.views_notification import ManualNotificationPreviewView
from apps.domains.students.models import Student

User = get_user_model()


class ManualNotificationContextSourceTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="manual-source", name="Manual Source", is_active=True)
        self.admin = User.objects.create_user(
            username="manual-source-owner",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="owner")

    def _post(self, data: dict):
        request = self.factory.post("/api/v1/messaging/manual-notification/preview/", data=data, format="json")
        force_authenticate(request, user=self.admin)
        request.user = self.admin
        request.tenant = self.tenant
        return ManualNotificationPreviewView.as_view()(request)

    def _student(self, ps_number: str, name: str, parent_phone: str) -> Student:
        user = User.objects.create_user(
            username=user_internal_username(self.tenant, ps_number),
            password="test1234",
            tenant=self.tenant,
            phone="01011112222",
            name=name,
        )
        return Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number=ps_number,
            name=name,
            phone="01011112222",
            parent_phone=parent_phone,
            omr_code=f"99{ps_number[-2:]}{ps_number[-2:]}99",
        )

    def _clinic_change_session(self):
        active_student = self._student("S020", "변경학생", "01088889999")
        cancelled_student = self._student("S021", "취소학생", "01077778888")
        session = Session.objects.create(
            tenant=self.tenant,
            title="보강 클리닉",
            date=date(2026, 6, 1),
            start_time=time(14, 30),
            location="2관",
            max_participants=10,
        )
        SessionParticipant.objects.create(
            tenant=self.tenant,
            session=session,
            student=active_student,
            status=SessionParticipant.Status.BOOKED,
        )
        SessionParticipant.objects.create(
            tenant=self.tenant,
            session=session,
            student=cancelled_student,
            status=SessionParticipant.Status.CANCELLED,
        )
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            category="clinic",
            name="클리닉 변경",
            subject="",
            body="#{학생이름} #{클리닉변동사항} #{클리닉수정자}",
        )
        AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_reservation_changed",
            template=template,
            enabled=False,
            message_mode="alimtalk",
        )
        return active_student, cancelled_student, session

    def test_manual_preview_resolves_clinic_session_change_context_source(self):
        active_student, _cancelled_student, session = self._clinic_change_session()

        response = self._post(
            {
                "trigger": "clinic_reservation_changed",
                "context_source": {
                    "type": "clinic_session_change",
                    "session_id": session.id,
                },
                "send_to": "parent",
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_count"], 1)
        recipient = response.data["recipients"][0]
        self.assertEqual(recipient["student_id"], active_student.id)
        self.assertIn("2026-06-01 14:30 2관", recipient["message_body"])
        self.assertIn(self.admin.username, recipient["message_body"])

        token = NotificationPreviewToken.objects.get(token=response.data["preview_token"])
        self.assertEqual(len(token.payload["recipients"]), 1)
        replacements = token.payload["recipients"][0]["alimtalk_replacements"]
        self.assertIn(
            {"key": "클리닉변동사항", "value": "2026-06-01 14:30 2관"},
            replacements,
        )

    def test_manual_preview_rejects_context_source_shared_context_override(self):
        _active_student, _cancelled_student, session = self._clinic_change_session()

        response = self._post(
            {
                "trigger": "clinic_reservation_changed",
                "context_source": {
                    "type": "clinic_session_change",
                    "session_id": session.id,
                },
                "context": {"클리닉변동사항": "임의 변경"},
                "send_to": "parent",
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("context_source가 생성한 변수", response.data["detail"])
        self.assertIn("context: 클리닉변동사항", response.data["detail"])

    def test_manual_preview_uses_context_source_old_schedule_snapshot(self):
        active_student, _cancelled_student, session = self._clinic_change_session()

        response = self._post(
            {
                "trigger": "clinic_reservation_changed",
                "context_source": {
                    "type": "clinic_session_change",
                    "session_id": session.id,
                    "old_schedule": "2026-05-31 13:00 1관",
                },
                "send_to": "parent",
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_count"], 1)
        self.assertEqual(response.data["recipients"][0]["student_id"], active_student.id)

        token = NotificationPreviewToken.objects.get(token=response.data["preview_token"])
        replacements = token.payload["recipients"][0]["alimtalk_replacements"]
        self.assertIn(
            {"key": "클리닉기존일정", "value": "2026-05-31 13:00 1관"},
            replacements,
        )

    def test_manual_preview_rejects_context_source_per_student_override(self):
        active_student, _cancelled_student, session = self._clinic_change_session()

        response = self._post(
            {
                "trigger": "clinic_reservation_changed",
                "context_source": {
                    "type": "clinic_session_change",
                    "session_id": session.id,
                },
                "context_per_student": {
                    str(active_student.id): {"클리닉수정자": "다른 사람"},
                },
                "send_to": "parent",
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("context_source가 생성한 변수", response.data["detail"])
        self.assertIn("context_per_student: 클리닉수정자", response.data["detail"])

    def test_manual_preview_rejects_context_source_for_wrong_trigger(self):
        response = self._post(
            {
                "trigger": "exam_score_published",
                "context_source": {"type": "clinic_session_change", "session_id": 1},
                "send_to": "parent",
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "clinic_session_change는 클리닉 변경 알림에만 사용할 수 있습니다.",
        )

    def test_missing_homework_notice_requires_assignment_and_rechecks_evidence_before_outbox(self):
        from unittest.mock import patch
        from django.urls import resolve
        from apps.domains.enrollment.test_support import create_enrollment_fixture, create_session_enrollment_fixture
        from apps.domains.homework.test_support import create_homework_assignment_fixture
        from apps.domains.homework_results.test_support import create_homework_fixture, create_homework_score_fixture
        from apps.domains.lectures.test_support import create_lecture_fixture, create_session_fixture
        from apps.domains.messaging.models import ScheduledNotification
        from apps.domains.progress.models import HomeworkFileAcceptance
        from apps.domains.submissions.models import Submission
        from apps.domains.messaging.notification_dispatch import consume_preview_token_and_execute

        student = self._student("S034", "과제학생", "01088889999")
        lecture = create_lecture_fixture(tenant=self.tenant, title="수학", name="수학", subject="MATH")
        session = create_session_fixture(lecture=lecture, order=1, title="1차시")
        enrollment = create_enrollment_fixture(tenant=self.tenant, student=student, lecture=lecture, status="ACTIVE")
        homework = create_homework_fixture(tenant=self.tenant, session=session, title="풀이 인증")
        create_homework_assignment_fixture(tenant=self.tenant, homework=homework, session=session, enrollment=enrollment)
        trigger = "assignment_not_submitted"
        template = MessageTemplate.objects.create(
            tenant=self.tenant, category="assignment", name="과제 안내", body="#{학생이름} #{과제명}",
        )
        AutoSendConfig.objects.create(tenant=self.tenant, trigger=trigger, template=template, message_mode="alimtalk")

        def preview(source):
            data = {"trigger": trigger, "send_to": "parent", "student_ids": [student.pk]}
            if source is not None:
                data["context_source"] = {"type": trigger, "homework_id": source}
            return self._post(data)

        self.assertEqual(preview(None).status_code, 400)
        self.assertEqual(preview(homework.pk + 1000).status_code, 400)
        score = create_homework_score_fixture(
            homework=homework, session=session, enrollment=enrollment, score=None,
            meta={"status": "NOT_SUBMITTED"},
        )
        first = preview(homework.pk)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual([r["student_id"] for r in first.data["recipients"]], [student.pk])
        token = first.data["preview_token"]
        self.assertTrue(token)
        create_session_enrollment_fixture(tenant=self.tenant, session=session, enrollment=enrollment)
        correction_path = f"/api/v1/results/admin/sessions/{session.pk}/score-correction/"

        def set_manual_completion(completed):
            request = self.factory.patch(correction_path, {
                "enrollment_id": enrollment.pk, "source_id": homework.pk, "source_type": "homework",
                "completed": completed, "note": "수기 확인",
            }, format="json")
            request.tenant = self.tenant
            force_authenticate(request, user=self.admin)
            match = resolve(correction_path)
            response = match.func(request, **match.kwargs)
            self.assertEqual(response.status_code, 200, response.data)

        set_manual_completion(True)
        self.assertEqual(preview(homework.pk).data["recipients"], [])
        self.assertEqual(consume_preview_token_and_execute(token, self.tenant)["code"], "preview_changed")
        set_manual_completion(False)
        self.assertEqual(preview(homework.pk).data["total_count"], 1)
        score.passed = True
        score.meta = {}
        score.save(update_fields=["passed", "meta"])
        self.assertEqual(preview(homework.pk).data["recipients"], [])
        score.passed = False
        score.save(update_fields=["passed"])
        initial_phone = student.parent_phone
        student.parent_phone = "01055550001"
        student.save(update_fields=["parent_phone"])
        self.assertEqual(consume_preview_token_and_execute(token, self.tenant)["code"], "preview_changed")
        student.parent_phone = initial_phone
        student.save(update_fields=["parent_phone"])
        submission = Submission.objects.create(
            tenant=self.tenant, user=student.user, enrollment=enrollment,
            target_type=Submission.TargetType.HOMEWORK, target_id=homework.pk,
            source=Submission.Source.HOMEWORK_IMAGE, status=Submission.Status.SUBMITTED, file_key="fixture/photo.jpg",
        )
        self.assertEqual(preview(homework.pk).data["recipients"], [])
        result = consume_preview_token_and_execute(token, self.tenant, session_type="manual")
        self.assertEqual((result.get("status"), result.get("code")), (409, "preview_changed"))
        self.assertFalse(ScheduledNotification.objects.filter(tenant=self.tenant).exists())
        submission.status = Submission.Status.FAILED
        submission.save(update_fields=["status"])
        score.score = 0
        score.save(update_fields=["score"])
        self.assertEqual(preview(homework.pk).data["recipients"], [])
        score.score = None
        score.save(update_fields=["score"])
        self.assertEqual(preview(homework.pk).data["total_count"], 1)
        HomeworkFileAcceptance.objects.create(
            tenant=self.tenant, session=session, enrollment=enrollment,
            homework=homework, fingerprint="1" * 64,
        )
        self.assertEqual(preview(homework.pk).data["recipients"], [])
        old_token = NotificationPreviewToken.objects.get(token=token)
        old_token.payload.pop("homework_id")
        old_token.save(update_fields=["payload"])
        self.assertEqual(consume_preview_token_and_execute(token, self.tenant)["code"], "preview_changed")
        self.assertFalse(ScheduledNotification.objects.filter(tenant=self.tenant).exists())

        control = self._student("S035", "미제출학생", "01077773333")
        control_enrollment = create_enrollment_fixture(tenant=self.tenant, student=control, lecture=lecture, status="ACTIVE")
        create_homework_assignment_fixture(tenant=self.tenant, homework=homework, session=session, enrollment=control_enrollment)
        actual = preview(homework.pk).data
        self.assertEqual([row["student_id"] for row in actual["recipients"]], [control.pk])
        # Another assignment becomes missing after preview. Confirm only the reviewed student.
        HomeworkFileAcceptance.objects.filter(tenant=self.tenant, enrollment=enrollment, homework=homework).delete()
        with patch("apps.domains.messaging.scheduled.process_due_notifications") as process:
            confirmed = consume_preview_token_and_execute(actual["preview_token"], self.tenant, session_type="manual")
        scheduled = ScheduledNotification.objects.get(tenant=self.tenant)
        self.assertEqual(scheduled.payload["target_id"], control.pk)
        self.assertEqual(scheduled.payload["to"], control.parent_phone)
        self.assertEqual(confirmed["batch_result"]["accepted_count"], 1)
        process.assert_called_once_with(batch_size=1, notification_ids=[scheduled.id])

    def test_grade_homework_preview_and_send_recheck_only_the_selected_assignment(self):
        import json
        from unittest.mock import patch
        from apps.domains.messaging.models import ScheduledNotification
        from apps.domains.messaging.views.operations_views import SendMessagePreflightView
        from apps.domains.messaging.views.send_views import SendMessageView
        from apps.domains.enrollment.test_support import create_enrollment_fixture, create_session_enrollment_fixture
        from apps.domains.homework.test_support import create_homework_assignment_fixture
        from apps.domains.homework_results.test_support import create_homework_fixture, create_homework_score_fixture
        from apps.domains.lectures.test_support import create_lecture_fixture, create_session_fixture
        from apps.domains.progress.models import HomeworkFileAcceptance
        from apps.domains.submissions.models import Submission
        from apps.support.messaging.session_message_context import resolve_session_message_data
        from apps.support.submissions.dependencies import homework_submission_revisions

        student = self._student("S039", "성적 확인 학생", "01088889999")
        lecture = create_lecture_fixture(tenant=self.tenant, title="수학", name="수학", subject="MATH")
        session = create_session_fixture(lecture=lecture, order=1, title="1차시")
        enrollment = create_enrollment_fixture(tenant=self.tenant, student=student, lecture=lecture)
        create_session_enrollment_fixture(tenant=self.tenant, session=session, enrollment=enrollment)
        tasks = [create_homework_fixture(tenant=self.tenant, session=session, title="연습", display_order=i) for i in (1, 2)]
        for homework in tasks:
            create_homework_assignment_fixture(tenant=self.tenant, session=session, enrollment=enrollment, homework=homework)
        scores = [create_homework_score_fixture(session=session, homework=hw, enrollment=enrollment,
            score=None, meta={"status": "NOT_SUBMITTED"}, passed=False) for hw in tasks]
        legacy_data = {"session_id": session.pk, "send_to": "parent", "block_category": "grades",
            "student_ids": [student.pk], "raw_body": "#{과제1결과} / #{과제2결과}",
            "alimtalk_extra_vars_per_student": {str(student.pk): {"_body_subst": "미제출 / 미제출"}}}
        def snapshot(state, rendered):
            context = {"_body_subst": rendered, "_homework_notice_states": json.dumps(
                [[1, tasks[0].pk, state], [2, tasks[1].pk, "missing"]])}
            return {**legacy_data, "alimtalk_extra_vars_per_student": {str(student.pk): context}}

        def post(data, final=False):
            url = "/api/v1/messaging/send/" if final else "/api/v1/messaging/send/preflight/"
            request = self.factory.post(url, data, format="json")
            force_authenticate(request, user=self.admin)
            request.tenant = self.tenant
            if final:
                with self.captureOnCommitCallbacks(execute=True):
                    return SendMessageView.as_view()(request)
            return SendMessagePreflightView.as_view()(request)

        outdated = snapshot("missing", "미제출 / 미제출")
        for data in (outdated, legacy_data):
            self.assertEqual(post(data).data["blockers"], [])
        submission = Submission.objects.create(tenant=self.tenant, user=student.user, enrollment=enrollment, target_type="homework",
            target_id=tasks[0].pk, source=Submission.Source.HOMEWORK_IMAGE, file_key="pending",
            status=Submission.Status.SUBMITTED)
        self.assertEqual(post(legacy_data).data["blockers"], [])
        submission.file_key = "test/file.jpg"
        submission.save(update_fields=["file_key"])
        with patch("apps.domains.messaging.services.enqueue_alimtalk", return_value=True) as provider:
            for data in (outdated, legacy_data):
                self.assertIn("grade_homework_review_changed", [i["code"] for i in post(data).data["blockers"]])
                denied = post(data, final=True)
                self.assertEqual((denied.status_code, denied.data.get("code")), (409,"grade_homework_review_changed"))
            provider.assert_not_called()
            self.assertFalse(ScheduledNotification.objects.exists())
            current = snapshot("other", "점수 미입력 / 미제출")
            self.assertEqual(post(snapshot("other", "미제출 / 미제출"), final=True).status_code, 409)
            resolved = resolve_session_message_data(tenant=self.tenant, data=current)
            self.assertNotIn("_homework_notice_states", resolved["alimtalk_extra_vars_per_student"][str(student.pk)])
            def legacy(raw, text):
                return {**legacy_data, "raw_body": raw, "alimtalk_extra_vars_per_student": {str(student.pk): {"_body_subst": text}}}
            legitimate = legacy("이전 미제출 안내와 별개입니다. #{과제2결과}", "이전 미제출 안내와 별개입니다. 미제출")
            adjoining = legacy("#{학생이름}#{과제1결과}#{선생님이름}", "미제출 문의 점수 미입력 선생님")
            for data in (current, legitimate, adjoining, legacy("#{과제목록}", "- 연습: 점수 미입력\n- 연습: 미제출")):
                self.assertEqual(post(data).data["blockers"], [])
            self.assertEqual(post(legacy("#{과제목록}", "- 연습: 미제출\n- 연습: 미제출"), final=True).status_code, 409)
            receipt = HomeworkFileAcceptance.objects.create(tenant=self.tenant, enrollment=enrollment, session=session,
                homework=tasks[0], fingerprint="9"*64)
            self.assertEqual(post(current, final=True).status_code, 409)
            self.assertEqual(post(snapshot("accepted", "통과 (교사 확인) / 미제출")).data["blockers"], [])
            receipt.delete()
            version = homework_submission_revisions(tenant=self.tenant,enrollment_ids=[enrollment.pk],homework_ids=[tasks[0].pk])
            scores[0].reviewed_submission_revision = version[(enrollment.pk, tasks[0].pk)]
            scores[0].save(update_fields=["reviewed_submission_revision"])
            self.assertEqual(post(outdated).data["blockers"], [])
            self.assertEqual(post(legitimate, final=True).status_code, 200)
            provider.assert_called_once()
            self.assertEqual(ScheduledNotification.objects.count(), 1)
