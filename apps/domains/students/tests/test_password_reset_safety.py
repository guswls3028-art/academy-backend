from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.core.models import Tenant
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student
from apps.domains.students.views.credential_views import SendExistingCredentialsView
from apps.domains.students.views.password_views import StudentPasswordResetSendView


class StudentPasswordResetSafetyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="비번안전학원", code="pw-safe")
        User = get_user_model()
        self.user = User.objects.create_user(
            username=user_internal_username(self.tenant, "S001"),
            password="oldpw123",
            tenant=self.tenant,
            must_change_password=False,
            token_version=0,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.user,
            ps_number="S001",
            omr_code="11112222",
            name="홍길동",
            phone="01011112222",
            parent_phone="01033334444",
        )

    def _post(self, view_class, path: str, data: dict):
        request = self.factory.post(path, data, format="json")
        request.tenant = self.tenant
        return view_class.as_view()(request)

    def test_invalid_skip_notify_does_not_change_password(self):
        response = self._post(
            StudentPasswordResetSendView,
            "/api/v1/students/password_reset_send/",
            {
                "target": "student",
                "student_name": self.student.name,
                "student_phone": self.student.phone,
                "skip_notify": "maybe",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpw123"))
        self.assertFalse(self.user.must_change_password)
        self.assertEqual(self.user.token_version, 0)

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=False)
    def test_password_reset_send_failure_restores_force_change_state(self, _send):
        response = self._post(
            StudentPasswordResetSendView,
            "/api/v1/students/password_reset_send/",
            {
                "target": "student",
                "student_name": self.student.name,
                "student_phone": self.student.phone,
            },
        )

        self.assertEqual(response.status_code, 503)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpw123"))
        self.assertFalse(self.user.must_change_password)
        self.assertEqual(self.user.token_version, 1)

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=False)
    def test_existing_credentials_send_failure_restores_force_change_state(self, _send):
        response = self._post(
            SendExistingCredentialsView,
            "/api/v1/students/send_existing_credentials/",
            {
                "name": self.student.name,
                "phone": self.student.phone,
            },
        )

        self.assertEqual(response.status_code, 503)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpw123"))
        self.assertFalse(self.user.must_change_password)
        self.assertEqual(self.user.token_version, 1)
