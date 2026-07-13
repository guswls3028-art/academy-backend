from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.billing.views import AdminDashboardView
from apps.core.models import Tenant


@override_settings(BILLING_EXEMPT_TENANT_IDS=set())
class AdminDashboardMetricTests(TestCase):
    def test_metrics_exclude_inactive_tenant_and_program(self):
        active = Tenant.objects.create(code="billing-mrr-active", name="Active", is_active=True)
        closed = Tenant.objects.create(code="billing-mrr-closed", name="Closed", is_active=False)
        program_off = Tenant.objects.create(code="billing-mrr-off", name="Off", is_active=True)
        for tenant in (active, closed, program_off):
            tenant.program.subscription_status = "active"
            tenant.program.monthly_price = 100_000
            tenant.program.save(update_fields=["subscription_status", "monthly_price"])
        program_off.program.is_active = False
        program_off.program.save(update_fields=["is_active"])
        user = get_user_model().objects.create_superuser(
            username="billing-dashboard-admin",
            password="test1234",
            tenant=active,
        )
        request = APIRequestFactory().get("/api/v1/billing/admin/dashboard/")
        force_authenticate(request, user=user)

        response = AdminDashboardView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["mrr"], 100_000)
        self.assertEqual(response.data["total_tenants"], 1)
        self.assertEqual(response.data["status_counts"], {"active": 1})
