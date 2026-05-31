from datetime import date, time
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.clinic.models import Session, SessionParticipant
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, NotificationPreviewToken
from unittest.mock import patch

from apps.domains.messaging.notification_dispatch import execute_notification_batch
from apps.domains.messaging.views_notification import (
    AttendanceNotificationPreviewView,
    ManualNotificationPreviewView,
)
from apps.domains.students.models import Student


User = get_user_model()


class NotificationPreviewViewValidationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="msg-preview", name="Msg Preview", is_active=True)
        self.admin = User.objects.create_user(
            username="msg-preview-owner",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="owner")

    def _post(self, view, path: str, data: dict):
        request = self.factory.post(path, data=data, format="json")
        force_authenticate(request, user=self.admin)
        request.user = self.admin
        request.tenant = self.tenant
        return view.as_view()(request)

    def test_attendance_preview_rejects_invalid_session_id(self):
        response = self._post(
            AttendanceNotificationPreviewView,
            "/api/v1/messaging/attendance-notification/preview/",
            {"session_id": "abc", "notification_type": "check_in", "send_to": "parent"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "session_id는 양의 정수여야 합니다.")

    def test_attendance_preview_rejects_invalid_send_to(self):
        response = self._post(
            AttendanceNotificationPreviewView,
            "/api/v1/messaging/attendance-notification/preview/",
            {"session_id": 1, "notification_type": "check_in", "send_to": "staff"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "send_to는 'parent' 또는 'student'만 가능합니다.")

    def test_manual_preview_rejects_invalid_send_to(self):
        response = self._post(
            ManualNotificationPreviewView,
            "/api/v1/messaging/manual-notification/preview/",
            {"trigger": "exam_score_published", "student_ids": [1], "send_to": "staff"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "send_to는 'parent' 또는 'student'만 가능합니다.")

    def test_manual_preview_rejects_invalid_student_ids(self):
        response = self._post(
            ManualNotificationPreviewView,
            "/api/v1/messaging/manual-notification/preview/",
            {"trigger": "exam_score_published", "student_ids": [1, "bad"], "send_to": "parent"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "student_ids는 양의 정수 목록이어야 합니다.")

    def test_manual_preview_rejects_non_object_context(self):
        response = self._post(
            ManualNotificationPreviewView,
            "/api/v1/messaging/manual-notification/preview/",
            {
                "trigger": "exam_score_published",
                "student_ids": [1],
                "send_to": "parent",
                "context": ["bad"],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "context는 객체여야 합니다.")

    def test_manual_preview_rejects_non_object_context_per_student(self):
        response = self._post(
            ManualNotificationPreviewView,
            "/api/v1/messaging/manual-notification/preview/",
            {
                "trigger": "exam_score_published",
                "student_ids": [1],
                "send_to": "parent",
                "context_per_student": ["bad"],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "context_per_student는 객체여야 합니다.")

    def test_manual_preview_zero_sendable_recipients_hides_internal_fields(self):
        student_user = User.objects.create_user(
            username=user_internal_username(self.tenant, "S001"),
            password="test1234",
            tenant=self.tenant,
            phone="",
            name="전화없음",
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            ps_number="S001",
            name="전화없음",
            phone="",
            parent_phone="",
            omr_code="11112222",
        )
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            category="grades",
            name="성적 안내",
            subject="",
            body="성적 안내 #{학생이름}",
            solapi_template_id="APPROVED-SID",
            solapi_status="APPROVED",
        )
        AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="exam_score_published",
            template=template,
            enabled=False,
            message_mode="alimtalk",
        )

        response = self._post(
            ManualNotificationPreviewView,
            "/api/v1/messaging/manual-notification/preview/",
            {"trigger": "exam_score_published", "student_ids": [student.id], "send_to": "parent"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["preview_token"])
        self.assertEqual(response.data["total_count"], 0)
        self.assertEqual(response.data["excluded_count"], 1)
        recipient = response.data["recipients"][0]
        self.assertNotIn("phone_raw", recipient)
        self.assertNotIn("alimtalk_replacements", recipient)

    def test_manual_preview_omits_deleted_and_cross_tenant_students(self):
        active_user = User.objects.create_user(
            username=user_internal_username(self.tenant, "S010"),
            password="test1234",
            tenant=self.tenant,
            phone="01010101010",
            name="활성학생",
        )
        active_student = Student.objects.create(
            tenant=self.tenant,
            user=active_user,
            ps_number="S010",
            name="활성학생",
            phone="01010101010",
            parent_phone="01055556666",
            omr_code="10101010",
        )
        deleted_user = User.objects.create_user(
            username=user_internal_username(self.tenant, "S011"),
            password="test1234",
            tenant=self.tenant,
            phone="01020202020",
            name="삭제학생",
        )
        deleted_student = Student.objects.create(
            tenant=self.tenant,
            user=deleted_user,
            ps_number="S011",
            name="삭제학생",
            phone="01020202020",
            parent_phone="01066667777",
            omr_code="20202020",
            deleted_at=timezone.now(),
        )
        other_tenant = Tenant.objects.create(code="msg-preview-other", name="Other", is_active=True)
        other_user = User.objects.create_user(
            username=user_internal_username(other_tenant, "S999"),
            password="test1234",
            tenant=other_tenant,
            phone="01099998888",
            name="타원생",
        )
        other_student = Student.objects.create(
            tenant=other_tenant,
            user=other_user,
            ps_number="S999",
            name="타원생",
            phone="01099998888",
            parent_phone="01077778888",
            omr_code="99998888",
        )
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            category="grades",
            name="성적 안내",
            subject="",
            body="성적 안내 #{학생이름}",
            solapi_template_id="APPROVED-SID",
            solapi_status="APPROVED",
        )
        AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="exam_score_published",
            template=template,
            enabled=False,
            message_mode="alimtalk",
        )

        response = self._post(
            ManualNotificationPreviewView,
            "/api/v1/messaging/manual-notification/preview/",
            {
                "trigger": "exam_score_published",
                "student_ids": [
                    active_student.id,
                    deleted_student.id,
                    other_student.id,
                    active_student.id,
                ],
                "send_to": "parent",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_count"], 1)
        self.assertEqual(response.data["excluded_count"], 0)
        self.assertEqual(len(response.data["recipients"]), 1)
        recipient = response.data["recipients"][0]
        self.assertEqual(recipient["student_id"], active_student.id)
        self.assertEqual(recipient["phone"], "010****6666")
        self.assertNotIn("phone_raw", recipient)
        self.assertNotIn("alimtalk_replacements", recipient)

        token = NotificationPreviewToken.objects.get(token=response.data["preview_token"])
        payload_recipient = token.payload["recipients"][0]
        self.assertEqual(payload_recipient["student_id"], active_student.id)
        self.assertEqual(payload_recipient["phone_raw"], "01055556666")

    def test_manual_preview_resolves_clinic_session_change_context_source(self):
        active_user = User.objects.create_user(
            username=user_internal_username(self.tenant, "S020"),
            password="test1234",
            tenant=self.tenant,
            phone="01011112222",
            name="변경학생",
        )
        active_student = Student.objects.create(
            tenant=self.tenant,
            user=active_user,
            ps_number="S020",
            name="변경학생",
            phone="01011112222",
            parent_phone="01088889999",
            omr_code="20202020",
        )
        cancelled_user = User.objects.create_user(
            username=user_internal_username(self.tenant, "S021"),
            password="test1234",
            tenant=self.tenant,
            phone="01022223333",
            name="취소학생",
        )
        cancelled_student = Student.objects.create(
            tenant=self.tenant,
            user=cancelled_user,
            ps_number="S021",
            name="취소학생",
            phone="01022223333",
            parent_phone="01077778888",
            omr_code="21212121",
        )
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

        response = self._post(
            ManualNotificationPreviewView,
            "/api/v1/messaging/manual-notification/preview/",
            {
                "trigger": "clinic_reservation_changed",
                "context_source": {
                    "type": "clinic_session_change",
                    "session_id": session.id,
                },
                "send_to": "parent",
            },
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

    def test_manual_preview_rejects_context_source_for_wrong_trigger(self):
        response = self._post(
            ManualNotificationPreviewView,
            "/api/v1/messaging/manual-notification/preview/",
            {
                "trigger": "exam_score_published",
                "context_source": {"type": "clinic_session_change", "session_id": 1},
                "send_to": "parent",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "clinic_session_change는 클리닉 변경 알림에만 사용할 수 있습니다.",
        )


class NotificationBatchDispatchPolicyTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="msg-batch", name="Msg Batch", is_active=True)

    @patch("apps.domains.messaging.policy.check_recipient_allowed", return_value=True)
    @patch("apps.domains.messaging.services.enqueue_sms", return_value=True)
    def test_legacy_sms_preview_payload_is_sent_as_alimtalk_only(self, mock_enqueue, _mock_allowed):
        result = execute_notification_batch(
            tenant=self.tenant,
            payload={
                "recipients": [
                    {
                        "student_id": 11,
                        "student_name": "정합성",
                        "phone_raw": "01012345678",
                        "message_body": "알림 본문",
                        "alimtalk_replacements": [{"key": "선생님메모", "value": "알림 본문"}],
                    }
                ],
                "solapi_template_id": "KA01TP_TEST",
                "message_mode": "sms",
                "notification_type": "check_in",
            },
            batch_id="batch-legacy-sms",
            staff_id=7,
        )

        self.assertEqual(result["sent_count"], 1)
        mock_enqueue.assert_called_once()
        kwargs = mock_enqueue.call_args.kwargs
        self.assertEqual(kwargs["message_mode"], "alimtalk")
        self.assertEqual(kwargs["template_id"], "KA01TP_TEST")
        self.assertEqual(kwargs["target_type"], "parent")

    @patch("apps.domains.messaging.policy.check_recipient_allowed", return_value=True)
    @patch("apps.domains.messaging.services.enqueue_sms", return_value=True)
    def test_preview_batch_marks_student_target_when_sending_to_student(self, mock_enqueue, _mock_allowed):
        result = execute_notification_batch(
            tenant=self.tenant,
            payload={
                "recipients": [
                    {
                        "student_id": 12,
                        "student_name": "학생수신",
                        "phone_raw": "01012345678",
                        "message_body": "학생 안내",
                        "alimtalk_replacements": [{"key": "선생님메모", "value": "학생 안내"}],
                    }
                ],
                "solapi_template_id": "KA01TP_TEST",
                "message_mode": "alimtalk",
                "notification_type": "clinic_reminder",
                "send_to": "student",
            },
            batch_id="batch-student-target",
            staff_id=7,
        )

        self.assertEqual(result["sent_count"], 1)
        self.assertEqual(mock_enqueue.call_args.kwargs["target_type"], "student")
