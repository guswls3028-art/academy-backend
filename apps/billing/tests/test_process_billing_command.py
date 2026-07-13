from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.billing.management.commands.process_billing import Command
from apps.billing.models import Invoice
from apps.billing.services.invoice_service import InvoiceTransitionError
from apps.core.models import Tenant
from apps.core.models.program import Program


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

    @override_settings(
        BILLING_EXEMPT_TENANT_IDS=set(),
        TOSS_AUTO_BILLING_ENABLED=False,
    )
    def test_due_invoice_request_transitions_to_pending_without_charging(self):
        manual = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-BATCH-MANUAL-001",
            plan="pro",
            billing_mode="INVOICE_REQUEST",
            supply_amount=150_000,
            tax_amount=15_000,
            total_amount=165_000,
            period_start=date.today() - timedelta(days=30),
            period_end=date.today() - timedelta(days=1),
            due_date=date.today(),
            status="SCHEDULED",
        )

        call_command("process_billing", stdout=StringIO())

        manual.refresh_from_db()
        self.assertEqual(manual.status, "PENDING")
        self.assertFalse(manual.transactions.exists())

    @override_settings(
        BILLING_EXEMPT_TENANT_IDS=set(),
        BILLING_GRACE_PERIOD_DAYS=7,
        TOSS_AUTO_BILLING_ENABLED=False,
    )
    def test_expired_active_subscription_enters_grace_in_primary_batch(self):
        program = self.tenant.program
        program.subscription_status = "active"
        program.subscription_expires_at = date.today() - timedelta(days=1)
        program.next_billing_at = None
        program.save(
            update_fields=[
                "subscription_status",
                "subscription_expires_at",
                "next_billing_at",
            ]
        )

        call_command("process_billing", stdout=StringIO())

        program.refresh_from_db()
        self.assertEqual(program.subscription_status, "grace")
        self.assertTrue(program.is_subscription_active)
        self.assertEqual(
            program.grace_expires_at,
            date.today() + timedelta(days=6),
        )

    @override_settings(
        BILLING_EXEMPT_TENANT_IDS=set(),
        BILLING_GRACE_PERIOD_DAYS=7,
        TOSS_AUTO_BILLING_ENABLED=False,
    )
    def test_legacy_grace_cancel_expires_without_waiting_for_grace_end(self):
        program = self.tenant.program
        program.subscription_status = "grace"
        program.subscription_expires_at = date.today() - timedelta(days=1)
        program.next_billing_at = None
        program.cancel_at_period_end = True
        program.save(
            update_fields=[
                "subscription_status",
                "subscription_expires_at",
                "next_billing_at",
                "cancel_at_period_end",
            ]
        )

        call_command("process_billing", stdout=StringIO())

        program.refresh_from_db()
        self.assertEqual(program.subscription_status, "expired")

    @override_settings(
        BILLING_EXEMPT_TENANT_IDS=set(),
        TOSS_AUTO_BILLING_ENABLED=False,
    )
    def test_price_conflict_blocks_only_affected_tenant_invoice(self):
        conflict_tenant = Tenant.objects.create(
            name="Contract Conflict",
            code="ymath",
            is_active=True,
        )
        conflict_program = conflict_tenant.program
        conflict_program.subscription_expires_at = date.today() + timedelta(days=2)
        conflict_program.next_billing_at = date.today()
        conflict_program.save(
            update_fields=["subscription_expires_at", "next_billing_at"]
        )
        Program.objects.filter(pk=conflict_program.pk).update(monthly_price=198_000)

        valid_tenant = Tenant.objects.create(
            name="Valid Billing",
            code="valid_billing",
            is_active=True,
        )
        valid_program = valid_tenant.program
        valid_program.subscription_expires_at = date.today() + timedelta(days=2)
        valid_program.next_billing_at = date.today()
        valid_program.save(
            update_fields=["subscription_expires_at", "next_billing_at"]
        )
        output = StringIO()

        call_command("process_billing", stdout=output)

        self.assertFalse(Invoice.objects.filter(tenant=conflict_tenant).exists())
        self.assertTrue(Invoice.objects.filter(tenant=valid_tenant).exists())
        self.assertIn("[BLOCK] ymath", output.getvalue())
        self.assertIn("blocked_price=1", output.getvalue())

    @override_settings(
        BILLING_EXEMPT_TENANT_IDS=set(),
        TOSS_AUTO_BILLING_ENABLED=True,
    )
    @patch("apps.billing.management.commands.process_billing.payment_service.execute_auto_payment")
    def test_cancelled_subscription_still_collects_current_period_receivable(self, execute_payment):
        execute_payment.return_value = {
            "success": False,
            "reason": "declined",
        }
        self.invoice.next_retry_at = date.today()
        self.invoice.save(update_fields=["next_retry_at"])
        program = self.tenant.program
        program.cancel_at_period_end = True
        program.subscription_expires_at = date.today() + timedelta(days=10)
        program.next_billing_at = None
        program.save(
            update_fields=[
                "cancel_at_period_end",
                "subscription_expires_at",
                "next_billing_at",
            ]
        )

        call_command("process_billing", stdout=StringIO())

        execute_payment.assert_called_once_with(self.invoice.pk)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "FAILED")

    @override_settings(
        BILLING_EXEMPT_TENANT_IDS=set(),
        TOSS_AUTO_BILLING_ENABLED=True,
    )
    @patch("apps.billing.management.commands.process_billing.payment_service.execute_auto_payment")
    def test_cancelled_subscription_excludes_future_period_retry(self, execute_payment):
        program = self.tenant.program
        program.cancel_at_period_end = True
        program.subscription_expires_at = date.today() + timedelta(days=10)
        program.next_billing_at = None
        program.save(
            update_fields=[
                "cancel_at_period_end",
                "subscription_expires_at",
                "next_billing_at",
            ]
        )
        self.invoice.next_retry_at = date.today()
        self.invoice.period_start = program.subscription_expires_at + timedelta(days=1)
        self.invoice.period_end = self.invoice.period_start + timedelta(days=30)
        self.invoice.save(
            update_fields=["next_retry_at", "period_start", "period_end"]
        )

        call_command("process_billing", stdout=StringIO())

        execute_payment.assert_not_called()

    @patch(
        "apps.billing.management.commands.process_billing."
        "payment_service.execute_auto_payment",
        side_effect=RuntimeError("unexpected provider bug"),
    )
    def test_unexpected_payment_exception_propagates_nonzero(self, execute_payment):
        with self.assertRaisesRegex(RuntimeError, "unexpected provider bug"):
            Command()._execute_auto_payment_safely(self.invoice)

    @override_settings(
        BILLING_EXEMPT_TENANT_IDS=set(),
        TOSS_AUTO_BILLING_ENABLED=False,
    )
    def test_inactive_tenant_is_not_invoiced(self):
        program = self.tenant.program
        program.next_billing_at = date.today()
        program.subscription_expires_at = date.today() + timedelta(days=5)
        program.save(update_fields=["next_billing_at", "subscription_expires_at"])
        self.tenant.is_active = False
        self.tenant.save(update_fields=["is_active"])
        Invoice.objects.all().delete()

        call_command("process_billing", stdout=StringIO())

        self.assertFalse(Invoice.objects.filter(tenant=self.tenant).exists())

    @override_settings(
        BILLING_EXEMPT_TENANT_IDS=set(),
        TOSS_AUTO_BILLING_ENABLED=False,
    )
    def test_inactive_program_is_not_invoiced(self):
        program = self.tenant.program
        program.next_billing_at = date.today()
        program.subscription_expires_at = date.today() + timedelta(days=5)
        program.is_active = False
        program.save(
            update_fields=[
                "next_billing_at",
                "subscription_expires_at",
                "is_active",
            ]
        )
        Invoice.objects.all().delete()

        call_command("process_billing", stdout=StringIO())

        self.assertFalse(Invoice.objects.filter(tenant=self.tenant).exists())

    @override_settings(
        BILLING_EXEMPT_TENANT_IDS=set(),
        TOSS_AUTO_BILLING_ENABLED=True,
    )
    @patch("apps.billing.management.commands.process_billing.payment_service.execute_auto_payment")
    def test_inactive_tenant_existing_invoice_is_not_charged(self, execute_payment):
        self.invoice.status = "PENDING"
        self.invoice.save(update_fields=["status"])
        self.tenant.is_active = False
        self.tenant.save(update_fields=["is_active"])

        call_command("process_billing", stdout=StringIO())

        execute_payment.assert_not_called()

    @override_settings(
        BILLING_EXEMPT_TENANT_IDS=set(),
        TOSS_AUTO_BILLING_ENABLED=True,
    )
    @patch("apps.billing.management.commands.process_billing.payment_service.execute_auto_payment")
    def test_inactive_program_existing_invoice_is_not_charged(self, execute_payment):
        self.invoice.status = "PENDING"
        self.invoice.save(update_fields=["status"])
        program = self.tenant.program
        program.is_active = False
        program.save(update_fields=["is_active"])

        call_command("process_billing", stdout=StringIO())

        execute_payment.assert_not_called()
