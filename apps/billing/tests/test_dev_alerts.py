from __future__ import annotations

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing.models import Invoice, PaymentTransaction
from apps.core.management.commands.check_dev_alerts import (
    rule_card_reconciliation_required,
    rule_partial_refund_reconciliation_required,
    rule_stale_processing_payments,
)
from apps.core.models import OpsAuditLog, Tenant


@override_settings(BILLING_EXEMPT_TENANT_IDS=set(), OWNER_TENANT_ID=None)
class StaleProcessingPaymentAlertTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="payment-alert",
            name="Payment Alert",
            is_active=True,
        )
        self.invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-PROCESSING-ALERT",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=100_000,
            tax_amount=10_000,
            total_amount=110_000,
            period_start=timezone.localdate(),
            period_end=timezone.localdate(),
            due_date=timezone.localdate(),
            status="PENDING",
        )

    def _transaction(self, *, started_at):
        return PaymentTransaction.objects.create(
            tenant=self.tenant,
            invoice=self.invoice,
            provider="tosspayments",
            provider_order_id=self.invoice.provider_order_id,
            idempotency_key=self.invoice.provider_order_id,
            amount=self.invoice.total_amount,
            status="PROCESSING",
            processing_started_at=started_at,
        )

    def test_stale_processing_payment_triggers_metadata_only_alert(self):
        tx = self._transaction(started_at=timezone.now() - timedelta(minutes=16))

        result = rule_stale_processing_payments(min_age_minutes=15)

        self.assertIsNotNone(result)
        row = result["rows"][0]
        self.assertEqual(row["tenant"], self.tenant.code)
        self.assertEqual(row["transaction_id"], tx.id)
        self.assertEqual(row["invoice"], self.invoice.invoice_number)
        self.assertNotIn("provider_order_id", row)
        self.assertNotIn("payment_key", row)

    def test_recent_processing_payment_does_not_alert(self):
        self._transaction(started_at=timezone.now() - timedelta(minutes=2))

        self.assertIsNone(rule_stale_processing_payments(min_age_minutes=15))

    def test_card_reconciliation_evidence_triggers_metadata_only_alert(self):
        OpsAuditLog.objects.create(
            action="billing.card_reconciliation_required",
            summary="Card issue requires provider reconciliation",
            target_tenant=self.tenant,
            payload={"operation": "issue", "billing_key_id": None},
            result="failed",
        )

        result = rule_card_reconciliation_required()

        self.assertIsNotNone(result)
        row = result["rows"][0]
        self.assertEqual(row["tenant"], self.tenant.code)
        self.assertEqual(row["operation"], "issue")
        self.assertNotIn("provider_key", row)
        self.assertNotIn("customer_key", row)

    def test_partial_refund_evidence_triggers_immediate_alert(self):
        OpsAuditLog.objects.create(
            action="billing.partial_refund_reconciliation_required",
            summary="partial refund tx=1 requires reconciliation",
            target_tenant=self.tenant,
            payload={
                "transaction_id": 1,
                "invoice_id": self.invoice.id,
                "refunded_amount": 10_000,
            },
            result="failed",
        )

        result = rule_partial_refund_reconciliation_required()

        self.assertIsNotNone(result)
        self.assertEqual(result["rows"][0]["transaction_id"], 1)
        self.assertNotIn("payment_key", result["rows"][0])
