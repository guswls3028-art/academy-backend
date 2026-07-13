from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.views.dev_dashboard import DevDashboardSummaryView


@override_settings(BILLING_EXEMPT_TENANT_IDS=set(), OWNER_TENANT_ID=None)
class DevDashboardBillingMetricTests(TestCase):
    def test_mrr_excludes_inactive_tenant_and_program(self):
        platform = Tenant.objects.create(
            code="mrr-platform",
            name="MRR Platform",
            is_active=True,
        )
        active = Tenant.objects.create(code="mrr-active", name="MRR Active", is_active=True)
        closed = Tenant.objects.create(code="mrr-closed", name="MRR Closed", is_active=False)
        inactive_program = Tenant.objects.create(
            code="mrr-program-off",
            name="MRR Program Off",
            is_active=True,
        )
        for tenant in (active, closed, inactive_program):
            tenant.program.subscription_status = "active"
            tenant.program.monthly_price = 100_000
            tenant.program.save(update_fields=["subscription_status", "monthly_price"])
        inactive_program.program.is_active = False
        inactive_program.program.save(update_fields=["is_active"])

        user = get_user_model().objects.create_superuser(
            username="dashboard-platform-admin",
            password="test1234",
            tenant=platform,
        )
        TenantMembership.ensure_active(tenant=platform, user=user, role="owner")
        request = APIRequestFactory().get("/api/v1/core/dev/dashboard/")
        request.tenant = platform
        force_authenticate(request, user=user)

        with override_settings(OWNER_TENANT_ID=platform.id):
            response = DevDashboardSummaryView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["billing"]["mrr"], 100_000)
