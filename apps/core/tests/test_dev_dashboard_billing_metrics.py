from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Program, Tenant, TenantMembership
from apps.core.views.dev_dashboard import DevDashboardSummaryView


@override_settings(BILLING_EXEMPT_TENANT_IDS=set(), OWNER_TENANT_ID=None)
class DevDashboardBillingMetricTests(TestCase):
    def test_mrr_uses_mixed_contract_prices_and_excludes_inactive_rows(self):
        platform = Tenant.objects.create(
            code="mrr-platform",
            name="MRR Platform",
            is_active=True,
        )
        standard = Tenant.objects.create(
            code="mrr-standard",
            name="MRR Standard",
            is_active=True,
        )
        august = Tenant.objects.create(
            code="mrr-august",
            name="MRR August",
            is_active=True,
        )
        closed = Tenant.objects.create(code="mrr-closed", name="MRR Closed", is_active=False)
        inactive_program = Tenant.objects.create(
            code="mrr-program-off",
            name="MRR Program Off",
            is_active=True,
        )
        Program.objects.filter(pk=standard.program.pk).update(
            created_at=datetime(2026, 9, 1, tzinfo=dt_timezone.utc),
            monthly_price=198_000,
            subscription_status="active",
        )
        Program.objects.filter(pk=august.program.pk).update(
            created_at=datetime(2026, 8, 15, tzinfo=dt_timezone.utc),
            monthly_price=145_000,
            subscription_status="active",
        )
        Program.objects.filter(pk=closed.program.pk).update(
            created_at=datetime(2026, 9, 1, tzinfo=dt_timezone.utc),
            monthly_price=198_000,
            subscription_status="active",
        )
        Program.objects.filter(pk=inactive_program.program.pk).update(
            created_at=datetime(2026, 9, 1, tzinfo=dt_timezone.utc),
            monthly_price=198_000,
            subscription_status="active",
            is_active=False,
        )

        user = get_user_model().objects.create_superuser(
            username="dashboard-platform-admin",
            password="test1234",
            tenant=platform,
        )
        TenantMembership.ensure_active(tenant=platform, user=user, role="owner")
        request = APIRequestFactory().get("/api/v1/core/dev/dashboard/")
        request.tenant = platform
        force_authenticate(request, user=user)

        with (
            override_settings(OWNER_TENANT_ID=platform.id),
            patch.dict(
                Program.PLAN_PRICES,
                {Program.Plan.ALL: 198_000},
                clear=True,
            ),
        ):
            response = DevDashboardSummaryView.as_view()(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["billing"]["mrr"], 343_000)
        self.assertEqual(response.data["billing"]["mrr_supply_amount"], 343_000)
        self.assertEqual(response.data["billing"]["mrr_tax_amount"], 33_800)
        self.assertEqual(response.data["billing"]["mrr_total_amount"], 376_800)
