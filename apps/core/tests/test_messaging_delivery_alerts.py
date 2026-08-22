from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.core.management.commands.check_dev_alerts import (
    rule_messaging_delivery_health,
)
from apps.core.models import Tenant
from apps.domains.messaging.models import NotificationLog


@override_settings(MESSAGING_PROVIDER_LOW_BALANCE_ALERT_THRESHOLD=10_000)
class MessagingDeliveryAlertTests(TestCase):
    def setUp(self):
        self.owner = Tenant.objects.create(
            code="alert-owner",
            name="Alert Owner",
            is_active=True,
        )
        self.customer = Tenant.objects.create(
            code="alert-customer",
            name="Alert Customer",
            is_active=True,
        )

    @patch("apps.domains.messaging.services.solapi_client.get_solapi_client")
    def test_healthy_balance_and_no_recent_rejection_is_clear(self, get_client):
        get_client.return_value.get_balance.return_value = SimpleNamespace(
            balance=Decimal("25000.00")
        )

        self.assertIsNone(rule_messaging_delivery_health())

    @patch("apps.domains.messaging.services.solapi_client.get_solapi_client")
    def test_low_provider_balance_alerts_without_sensitive_data(self, get_client):
        get_client.return_value.get_balance.return_value = SimpleNamespace(
            balance=Decimal("9000.00")
        )

        result = rule_messaging_delivery_health()

        self.assertIsNotNone(result)
        self.assertEqual(result["rows"][0]["provider_balance"], "9000.00")
        self.assertNotIn("phone", str(result["rows"]).lower())

    @patch("apps.domains.messaging.services.solapi_client.get_solapi_client")
    def test_recent_balance_rejection_identifies_business_tenant(self, get_client):
        get_client.return_value.get_balance.return_value = SimpleNamespace(
            balance=Decimal("25000.00")
        )
        NotificationLog.objects.create(
            tenant=self.owner,
            source_tenant=self.customer,
            status="retryable_failed",
            success=False,
            message_mode="alimtalk",
            failure_reason="('NotEnoughBalance', 'rejected')",
        )

        result = rule_messaging_delivery_health()

        self.assertIsNotNone(result)
        self.assertEqual(result["rows"][0]["tenant"], self.customer.code)
        self.assertEqual(result["rows"][0]["state"], "retryable_failed")
        self.assertEqual(result["rows"][0]["count"], 1)
