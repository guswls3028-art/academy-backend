from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate
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
