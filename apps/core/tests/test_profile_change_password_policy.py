from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.views.profile import ProfileViewSet


class ProfileChangePasswordPolicyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="프로필비번학원", code="profile-pw")
        User = get_user_model()
        self.user = User.objects.create_user(
            username=user_internal_username(self.tenant, "teacher1"),
            password="oldpw123",
            tenant=self.tenant,
        )
        TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant,
            role="teacher",
            is_active=True,
        )
        self.view = ProfileViewSet.as_view({"post": "change_password"})
        self.update_view = ProfileViewSet.as_view({"patch": "update_me"})

    def _post(self, data: dict):
        request = self.factory.post("/api/v1/core/profile/change-password/", data, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return self.view(request)

    def test_rejects_short_new_password(self):
        response = self._post({"old_password": "oldpw123", "new_password": "123"})

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpw123"))

    def test_rejects_same_new_password(self):
        response = self._post({"old_password": "oldpw123", "new_password": "oldpw123"})

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpw123"))

    def test_invalid_profile_payload_does_not_partially_change_username(self):
        original_username = self.user.username
        request = self.factory.patch(
            "/api/v1/core/profile/update_me/",
            {"username": "renamed-teacher", "name": "x" * 51},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)

        response = self.update_view(request)

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, original_username)
