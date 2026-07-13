from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing.models import Invoice, PaymentTransaction
from apps.core.models import Tenant


@override_settings(BILLING_EXEMPT_TENANT_IDS=set())
class ReconcileProcessingPaymentsCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="reconcile-payment",
            name="Reconcile Payment",
            is_active=True,
        )
        self.invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-RECONCILE-001",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=100_000,
            tax_amount=10_000,
            total_amount=110_000,
            period_start=timezone.localdate(),
            period_end=timezone.localdate() + timedelta(days=30),
            due_date=timezone.localdate(),
            status="PENDING",
        )
        self.tx = PaymentTransaction.objects.create(
            tenant=self.tenant,
            invoice=self.invoice,
            provider="tosspayments",
            provider_order_id=self.invoice.provider_order_id,
            idempotency_key=self.invoice.provider_order_id,
            amount=self.invoice.total_amount,
            status="PROCESSING",
            processing_started_at=timezone.now() - timedelta(minutes=20),
        )

    @patch(
        "apps.billing.management.commands.reconcile_processing_payments."
        "TossPaymentsClient.get_payment_by_order_id"
    )
    def test_done_query_reconciles_processing_transaction(self, query_payment):
        query_payment.return_value = {
            "success": True,
            "type": "BILLING",
            "orderId": self.invoice.provider_order_id,
            "paymentKey": "pay_reconciled",
            "status": "DONE",
            "totalAmount": self.invoice.total_amount,
        }

        call_command(
            "reconcile_processing_payments",
            min_age_minutes=15,
            stdout=StringIO(),
        )

        self.tx.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.tx.status, "SUCCESS")
        self.assertEqual(self.invoice.status, "PAID")
        self.assertIsNone(self.tx.processing_started_at)

    @patch(
        "apps.billing.management.commands.reconcile_processing_payments."
        "TossPaymentsClient.get_payment_by_order_id"
    )
    def test_query_failure_leaves_processing_for_later_reconciliation(self, query_payment):
        query_payment.return_value = {
            "success": False,
            "error_code": "UPSTREAM_5XX",
        }

        with self.assertRaisesRegex(CommandError, "payment_reconciliation_unresolved"):
            call_command(
                "reconcile_processing_payments",
                min_age_minutes=15,
                stdout=StringIO(),
                stderr=StringIO(),
            )

        self.tx.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.tx.status, "PROCESSING")
        self.assertEqual(self.invoice.status, "PENDING")

    @patch(
        "apps.billing.management.commands.reconcile_processing_payments."
        "TossPaymentsClient.get_payment_by_order_id"
    )
    def test_canceled_query_reconciles_unknown_payment_as_refunded(self, query_payment):
        query_payment.return_value = {
            "success": True,
            "type": "BILLING",
            "orderId": self.invoice.provider_order_id,
            "paymentKey": "pay_canceled",
            "status": "CANCELED",
            "totalAmount": self.invoice.total_amount,
        }

        call_command(
            "reconcile_processing_payments",
            min_age_minutes=15,
            stdout=StringIO(),
        )

        self.tx.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.tx.status, "REFUNDED")
        self.assertEqual(self.tx.refunded_amount, self.tx.amount)
        self.assertEqual(self.invoice.status, "VOID")

    @patch(
        "apps.billing.management.commands.reconcile_processing_payments."
        "TossPaymentsClient.get_payment_by_order_id"
    )
    def test_partial_refund_remains_unresolved_for_operator_policy(self, query_payment):
        self.invoice.status = "PAID"
        self.invoice.paid_at = timezone.now()
        self.invoice.save(update_fields=["status", "paid_at"])
        self.tx.status = "PARTIALLY_REFUNDED"
        self.tx.provider_payment_key = "pay_partial_reconcile"
        self.tx.refunded_amount = 10_000
        self.tx.save(
            update_fields=[
                "status",
                "provider_payment_key",
                "refunded_amount",
                "updated_at",
            ]
        )
        PaymentTransaction.objects.filter(pk=self.tx.pk).update(
            updated_at=timezone.now() - timedelta(minutes=20)
        )
        query_payment.return_value = {
            "success": True,
            "type": "BILLING",
            "orderId": self.invoice.provider_order_id,
            "paymentKey": "pay_partial_reconcile",
            "status": "PARTIAL_CANCELED",
            "totalAmount": self.invoice.total_amount,
            "balanceAmount": self.invoice.total_amount - 10_000,
        }

        with self.assertRaisesRegex(
            CommandError,
            "payment_reconciliation_unresolved",
        ):
            call_command(
                "reconcile_processing_payments",
                min_age_minutes=15,
                stdout=StringIO(),
            )

        self.tx.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.tx.status, "PARTIALLY_REFUNDED")
        self.assertEqual(self.invoice.status, "PAID")
