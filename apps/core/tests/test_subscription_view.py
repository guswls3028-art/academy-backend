from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.core.models.program import Program

User = get_user_model()


class SubscriptionViewAuthorizationTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Subscription DTO Academy",
            code="subscription_dto",
            is_active=True,
        )
        self.program = Program.objects.get(tenant=self.tenant)
        self.program.billing_email = "billing@example.com"
        self.program.billing_mode = "AUTO_CARD"
        self.program.next_billing_at = date(2026, 7, 1)
        self.program.cancel_at_period_end = True
        self.program.save(
            update_fields=[
                "billing_email",
                "billing_mode",
                "next_billing_at",
                "cancel_at_period_end",
            ]
        )
        self.headers = {
            "HTTP_HOST": "localhost",
            "HTTP_X_TENANT_CODE": self.tenant.code,
        }

    def _authenticate_owner(self, suffix="owner"):
        owner = User.objects.create_user(
            username=f"t{self.tenant.id}_{suffix}",
            password="test1234!",
            tenant=self.tenant,
            is_staff=True,
            is_active=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=owner,
            role="owner",
            is_active=True,
        )
        self.client.force_authenticate(user=owner)
        return owner

    def test_anonymous_subscription_is_rejected(self):
        response = self.client.get("/api/v1/core/subscription/", **self.headers)

        self.assertIn(response.status_code, (401, 403))

    def test_staff_subscription_keeps_billing_operations_fields(self):
        self._authenticate_owner()

        response = self.client.get("/api/v1/core/subscription/", **self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["billing_email"], "billing@example.com")
        self.assertEqual(response.data["billing_mode"], "AUTO_CARD")
        self.assertEqual(response.data["next_billing_at"], "2026-07-01")
        self.assertTrue(response.data["cancel_at_period_end"])

    def test_price_contract_exposes_supply_tax_and_total_without_fake_promo(self):
        self.tenant.code = "ymath"
        self.tenant.save(update_fields=["code"])
        self.program.monthly_price = 198_000
        self.program.save(update_fields=["monthly_price"])
        self._authenticate_owner("contract-owner")

        response = self.client.get(
            "/api/v1/core/subscription/",
            HTTP_HOST="localhost",
            HTTP_X_TENANT_CODE="ymath",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["monthly_price"], 150_000)
        self.assertEqual(response.data["monthly_supply_amount"], 150_000)
        self.assertEqual(response.data["monthly_tax_amount"], 15_000)
        self.assertEqual(response.data["monthly_total_amount"], 165_000)
        self.assertFalse(response.data["monthly_price_includes_tax"])
        self.assertEqual(response.data["vat_rate_percent"], 10)
        self.assertEqual(response.data["billing_price_policy"], "contract_override")
        self.assertTrue(response.data["is_contract_price"])
        self.assertEqual(response.data["billing_price_integrity"], "ok")
        self.assertTrue(response.data["is_billing_price_ready"])
        self.assertFalse(response.data["is_promo"])
        self.assertEqual(response.data["discount_rate"], 0)

    def test_contract_price_drift_is_explicitly_not_billing_ready(self):
        self.tenant.code = "ymath"
        self.tenant.save(update_fields=["code"])
        Program.objects.filter(pk=self.program.pk).update(monthly_price=198_000)
        self._authenticate_owner("drift-owner")

        response = self.client.get(
            "/api/v1/core/subscription/",
            HTTP_HOST="localhost",
            HTTP_X_TENANT_CODE="ymath",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["billing_price_integrity"],
            "contract_price_mismatch",
        )
        self.assertFalse(response.data["is_billing_price_ready"])

    @override_settings(BILLING_GRACE_PERIOD_DAYS=7)
    def test_grace_contract_exposes_actual_service_access_end(self):
        today = timezone.localdate()
        self.program.subscription_status = "grace"
        self.program.subscription_expires_at = today - timedelta(days=3)
        self.program.save(
            update_fields=["subscription_status", "subscription_expires_at"]
        )
        self._authenticate_owner("grace-owner")

        response = self.client.get("/api/v1/core/subscription/", **self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_subscription_active"])
        self.assertEqual(response.data["days_remaining"], 4)
        self.assertEqual(response.data["grace_period_days"], 7)
        self.assertEqual(
            response.data["grace_expires_at"],
            str(today + timedelta(days=4)),
        )
        self.assertEqual(
            response.data["service_access_expires_at"],
            str(today + timedelta(days=4)),
        )
