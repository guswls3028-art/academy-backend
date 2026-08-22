from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username


@override_settings(ALLOWED_HOSTS=["testserver"])
class TokenRefreshAccountStateTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Refresh Tenant",
            code="refresh-account-state",
            is_active=True,
        )
        self.user = get_user_model().objects.create_user(
            username=user_internal_username(self.tenant, "teacher1"),
            password="oldpw123",
            tenant=self.tenant,
            token_version=0,
        )
        self.membership = TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.user,
            role="teacher",
        )

    def _refresh(self, *, token_version=0):
        token = RefreshToken.for_user(self.user)
        token["tenant_id"] = self.tenant.id
        token["token_version"] = token_version
        token["mcp"] = False
        return APIClient().post(
            "/api/v1/token/refresh/",
            {"refresh": str(token)},
            format="json",
        )

    def test_active_account_refreshes_and_rotates(self):
        response = self._refresh()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["access"])
        self.assertTrue(response.data["refresh"])

    def test_stale_token_version_is_rejected_before_rotation(self):
        self.user.token_version = 1
        self.user.save(update_fields=["token_version"])

        response = self._refresh(token_version=0)

        self.assertEqual(response.status_code, 401)

    def test_inactive_membership_is_rejected(self):
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])

        response = self._refresh()

        self.assertEqual(response.status_code, 401)

    def test_inactive_user_is_rejected(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self._refresh()

        self.assertEqual(response.status_code, 401)

    def test_inactive_tenant_is_rejected(self):
        self.tenant.is_active = False
        self.tenant.save(update_fields=["is_active"])

        response = self._refresh()

        self.assertEqual(response.status_code, 401)

    def test_role_without_required_profile_is_rejected(self):
        self.membership.role = "student"
        self.membership.save(update_fields=["role"])

        response = self._refresh()

        self.assertEqual(response.status_code, 401)
