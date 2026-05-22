from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import PendingPasswordReset, Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.services.password import generate_temp_password
from apps.domains.students.models import Student
from apps.domains.students.views.credential_views import SendExistingCredentialsView
from apps.domains.students.views.password_views import (
    _pw_reset_cache_key,
    StudentPasswordFindRequestView,
    StudentPasswordResetSendView,
)


class StudentPasswordResetSafetyTests(TestCase):
    def setUp(self):
        cache.clear()
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

    @override_settings(
        ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
        TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
    )
    def test_stale_staff_jwt_cannot_use_privileged_reset_options(self):
        User = get_user_model()
        staff = User.objects.create_user(
            username=user_internal_username(self.tenant, "staff01"),
            password="staffpw123",
            tenant=self.tenant,
            token_version=0,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=staff, role="teacher")
        token = AccessToken.for_user(staff)
        token["tenant_id"] = self.tenant.id
        token["token_version"] = 0
        staff.token_version = 1
        staff.save(update_fields=["token_version"])

        response = APIClient().post(
            "/api/v1/students/password_reset_send/",
            {
                "target": "student",
                "student_name": self.student.name,
                "student_phone": self.student.phone,
                "temp_password": "44445555",
                "skip_notify": True,
            },
            format="json",
            HTTP_HOST="api.hakwonplus.com",
            HTTP_X_TENANT_CODE=self.tenant.code,
            HTTP_AUTHORIZATION=f"Bearer {str(token)}",
        )

        self.assertIn(response.status_code, (401, 403))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpw123"))
        self.assertFalse(self.user.must_change_password)
        self.assertFalse(PendingPasswordReset.objects.filter(user=self.user).exists())

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
    def test_public_password_reset_send_failure_keeps_current_password_state(self, _send):
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
        self.assertEqual(self.user.token_version, 0)
        self.assertFalse(PendingPasswordReset.objects.filter(user=self.user).exists())

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_public_password_reset_sends_to_verified_parent_phone(self, send_mock):
        response = self._post(
            StudentPasswordResetSendView,
            "/api/v1/students/password_reset_send/",
            {
                "target": "student",
                "student_name": self.student.name,
                "parent_phone": self.student.parent_phone,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_mock.call_args.kwargs["to"], self.student.parent_phone)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpw123"))
        self.assertFalse(self.user.must_change_password)
        self.assertTrue(PendingPasswordReset.objects.filter(user=self.user).exists())

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_public_password_reset_unknown_account_is_generic_noop(self, send_mock):
        response = self._post(
            StudentPasswordResetSendView,
            "/api/v1/students/password_reset_send/",
            {
                "target": "student",
                "student_name": "없는학생",
                "student_phone": "01099998888",
            },
        )

        self.assertEqual(response.status_code, 200)
        send_mock.assert_not_called()
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpw123"))
        self.assertFalse(self.user.must_change_password)
        self.assertFalse(PendingPasswordReset.objects.filter(user=self.user).exists())

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=False)
    def test_existing_credentials_send_failure_keeps_current_password_state(self, _send):
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
        self.assertEqual(self.user.token_version, 0)
        self.assertFalse(PendingPasswordReset.objects.filter(user=self.user).exists())

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_existing_credentials_sends_to_verified_parent_phone(self, send_mock):
        response = self._post(
            SendExistingCredentialsView,
            "/api/v1/students/send_existing_credentials/",
            {
                "name": self.student.name,
                "phone": self.student.parent_phone,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_mock.call_args.kwargs["to"], self.student.parent_phone)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpw123"))
        self.assertTrue(PendingPasswordReset.objects.filter(user=self.user).exists())

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True)
    def test_password_find_request_resets_previous_failure_counter(self, _send):
        key = _pw_reset_cache_key(self.tenant.id, self.student.phone)
        cache.set(f"{key}:fail", 4, timeout=600)

        response = self._post(
            StudentPasswordFindRequestView,
            "/api/v1/students/password_find/request/",
            {
                "name": self.student.name,
                "phone": self.student.phone,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(cache.get(f"{key}:fail"))
        payload = cache.get(key)
        self.assertEqual(payload["user_id"], self.user.id)

    @patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=False)
    def test_password_find_request_clears_otp_when_send_fails(self, _send):
        key = _pw_reset_cache_key(self.tenant.id, self.student.phone)

        response = self._post(
            StudentPasswordFindRequestView,
            "/api/v1/students/password_find/request/",
            {
                "name": self.student.name,
                "phone": self.student.phone,
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertIsNone(cache.get(key))
        self.assertIsNone(cache.get(f"{key}:fail"))

    def test_auto_temp_password_is_eight_digits(self):
        temp_password = generate_temp_password()

        self.assertRegex(temp_password, r"^\d{8}$")
