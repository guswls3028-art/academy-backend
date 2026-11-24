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
        from apps.domains.enrollment.test_support import create_enrollment_fixture
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
