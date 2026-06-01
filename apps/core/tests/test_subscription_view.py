from datetime import date

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.core.models.program import Program

User = get_user_model()


class SubscriptionViewPublicDtoTests(APITestCase):
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

    def test_anonymous_subscription_hides_billing_operations_fields(self):
        response = self.client.get("/api/v1/core/subscription/", **self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["tenant_code"], self.tenant.code)
        for field in (
            "billing_email",
            "billing_mode",
            "next_billing_at",
            "cancel_at_period_end",
            "canceled_at",
        ):
            self.assertNotIn(field, response.data)

    def test_staff_subscription_keeps_billing_operations_fields(self):
        owner = User.objects.create_user(
            username=f"t{self.tenant.id}_owner",
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

        response = self.client.get("/api/v1/core/subscription/", **self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["billing_email"], "billing@example.com")
        self.assertEqual(response.data["billing_mode"], "AUTO_CARD")
        self.assertEqual(response.data["next_billing_at"], "2026-07-01")
        self.assertTrue(response.data["cancel_at_period_end"])
