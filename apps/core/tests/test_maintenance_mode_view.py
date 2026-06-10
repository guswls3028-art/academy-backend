from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.core.models import OpsAuditLog, Program, Tenant, TenantMembership


User = get_user_model()


class MaintenanceModeViewTests(APITestCase):
    def setUp(self):
        self.platform_tenant = Tenant.objects.create(
            name="HakwonPlus",
            code="hakwonplus",
            is_active=True,
        )
        self.client_tenant = Tenant.objects.create(
            name="Client Academy",
            code="client-academy",
            is_active=True,
        )
        self.owner = User.objects.create_user(
            username="platform-owner",
            password="pw123456!",
            tenant=self.platform_tenant,
            is_staff=True,
            is_active=True,
        )
        TenantMembership.objects.create(
            tenant=self.platform_tenant,
            user=self.owner,
            role="owner",
            is_active=True,
        )
        self.client.force_authenticate(self.owner)
        self.headers = {
            "HTTP_HOST": "testserver",
            "HTTP_X_TENANT_CODE": self.platform_tenant.code,
        }

    def _settings(self):
        return override_settings(
            OWNER_TENANT_ID=self.platform_tenant.id,
            ALLOWED_HOSTS=["testserver"],
            TENANT_HEADER_CODE_ALLOWED_HOSTS=("testserver",),
        )

    def test_patch_enabled_true_is_blocked_without_setting_flags(self):
        client_program = Program.objects.get(tenant=self.client_tenant)
        client_program.feature_flags = {"section_mode": True}
        client_program.save(update_fields=["feature_flags"])

        with self._settings():
            response = self.client.patch(
                "/api/v1/core/maintenance-mode/",
                {"enabled": True},
                format="json",
                **self.headers,
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "global_maintenance_disabled")
        client_program.refresh_from_db()
        self.assertEqual(client_program.feature_flags, {"section_mode": True})
        self.assertTrue(
            OpsAuditLog.objects.filter(
                action="maintenance.toggle.blocked",
                result="failed",
            ).exists()
        )

    def test_patch_enabled_false_clears_maintenance_flag_only(self):
        client_program = Program.objects.get(tenant=self.client_tenant)
        client_program.feature_flags = {
            "maintenance_mode": True,
            "section_mode": True,
        }
        client_program.save(update_fields=["feature_flags"])

        with self._settings():
            response = self.client.patch(
                "/api/v1/core/maintenance-mode/",
                {"enabled": False},
                format="json",
                **self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["enabled_count"], 0)
        client_program.refresh_from_db()
        self.assertEqual(client_program.feature_flags, {"section_mode": True})
        self.assertTrue(
            OpsAuditLog.objects.filter(
                action="maintenance.toggle",
                summary="Maintenance mode OFF",
            ).exists()
        )
