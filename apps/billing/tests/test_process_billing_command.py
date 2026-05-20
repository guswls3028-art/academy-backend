from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase

from apps.billing.management.commands.process_billing import Command
from apps.billing.models import Invoice
from apps.billing.services.invoice_service import InvoiceTransitionError
from apps.core.models import Tenant


class ProcessBillingCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Batch Billing",
            code="batch_billing",
            is_active=True,
        )
        self.invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-BATCH-001",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=100_000,
            tax_amount=10_000,
            total_amount=110_000,
            period_start=date.today(),
            period_end=date.today() + timedelta(days=30),
            due_date=date.today(),
            status="FAILED",
        )

    @patch("apps.billing.management.commands.process_billing.payment_service.execute_auto_payment")
    def test_invoice_transition_error_is_isolated_per_invoice(self, execute_auto_payment):
        execute_auto_payment.side_effect = InvoiceTransitionError("FAILED -> PAID")

        result = Command()._execute_auto_payment_safely(self.invoice)

        self.assertFalse(result["success"])
        self.assertEqual(result["invoice_id"], self.invoice.id)
        self.assertIn("state_changed", result["reason"])
