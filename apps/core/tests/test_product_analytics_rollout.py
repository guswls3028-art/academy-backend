from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Program, Tenant, TenantMembership
from apps.core.views.tenant_management import TenantDetailView


class ProductAnalyticsRolloutTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.platform = Tenant.objects.create(
            code="rollout-platform",
            name="Rollout Platform",
            is_active=True,
        )
        self.target = Tenant.objects.create(
            code="rollout-target",
            name="Rollout Target",
            is_active=True,
        )
        self.user = get_user_model().objects.create_superuser(
            username="rollout-owner",
            password="test1234",
            tenant=self.platform,
        )
        TenantMembership.objects.create(
            tenant=self.platform,
            user=self.user,
            role="owner",
            is_active=True,
        )

    def request(self, enabled: bool, **extra):
        request = self.factory.patch(
            f"/api/v1/core/tenants/{self.target.id}/",
            {"productUsageAnalyticsEnabled": enabled, **extra},
            format="json",
        )
        request.tenant = self.platform
        force_authenticate(request, user=self.user)
        return TenantDetailView.as_view()(request, tenant_id=self.target.id)

    @override_settings(PRODUCT_ANALYTICS_HASH_KEY="")
    def test_enable_is_blocked_until_dedicated_hash_key_exists(self):
        with override_settings(OWNER_TENANT_ID=self.platform.id):
            response = self.request(True)

        self.assertEqual(response.status_code, 409)
        flags = Program.objects.get(tenant=self.target).feature_flags
        self.assertNotIn("product_usage_analytics_enabled", flags)

    @override_settings(PRODUCT_ANALYTICS_HASH_KEY="")
    def test_blocked_enable_does_not_apply_other_tenant_changes(self):
        with override_settings(OWNER_TENANT_ID=self.platform.id):
            response = self.request(True, name="Should Not Persist")

        self.assertEqual(response.status_code, 409)
        self.target.refresh_from_db()
        self.assertEqual(self.target.name, "Rollout Target")

    @override_settings(PRODUCT_ANALYTICS_HASH_KEY="rollout-test-key")
    def test_platform_owner_can_enable_one_tenant_and_preserve_flags(self):
        program = Program.objects.get(tenant=self.target)
        program.feature_flags = {"section_mode": True}
        program.save(update_fields=["feature_flags"])

        with override_settings(OWNER_TENANT_ID=self.platform.id):
            response = self.request(True)

        self.assertEqual(response.status_code, 200, response.data)
        flags = Program.objects.get(tenant=self.target).feature_flags
        self.assertTrue(flags["section_mode"])
        self.assertTrue(flags["product_usage_analytics_enabled"])
        self.assertTrue(response.data["featureFlags"]["product_usage_analytics_enabled"])
