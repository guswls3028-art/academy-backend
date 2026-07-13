"""
InvoiceService 단위 테스트.

테스트 범위:
- 인보이스 생성 (기간 계산, 중복 방지)
- 상태 전이 (SCHEDULED→PENDING→PAID/FAILED/OVERDUE/VOID)
- mark_paid → 구독 갱신 연동
- 잘못된 전이 차단
- exempt 테넌트 제외
"""

from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.billing.models import Invoice, PaymentTransaction
from apps.billing.services import invoice_service
from apps.billing.services.invoice_service import (
    BillingPriceIntegrityError,
    InvoiceTransitionError,
)
from apps.core.models.program import Program
from apps.core.models.tenant import Tenant


class InvoiceServiceTestBase(TestCase):

    def setUp(self):
        # 기본 설정의 exempt id(1, 2)를 먼저 소비해 invoice 생성 테스트 대상은 live tenant가 되게 한다.
        Tenant.objects.create(name="시스템 테넌트", code="invoice_exempt_1", is_active=True)
        Tenant.objects.create(name="테스트 테넌트", code="invoice_exempt_2", is_active=True)
        self.tenant = Tenant.objects.create(
            name="테스트학원", code="test_inv", is_active=True
        )
        self.program = Program.objects.get(tenant=self.tenant)
        self.program.subscription_status = "active"
        self.program.subscription_started_at = date(2026, 3, 13)
        self.program.subscription_expires_at = date(2026, 4, 12)
        self.program.plan = "pro"
        self.program.monthly_price = 198_000
        self.program.billing_mode = "AUTO_CARD"
        self.program.save()


class TestCreateInvoice(InvoiceServiceTestBase):

    def test_create_for_next_period(self):
        inv = invoice_service.create_for_next_period(self.program)
        self.assertIsNotNone(inv)
        self.assertEqual(inv.tenant_id, self.tenant.id)
        self.assertEqual(inv.plan, "pro")
        self.assertEqual(inv.period_start, date(2026, 4, 13))
        self.assertEqual(inv.period_end, date(2026, 5, 12))
        self.assertEqual(inv.supply_amount, 198_000)
        self.assertEqual(inv.tax_amount, 19_800)
        self.assertEqual(inv.total_amount, 217_800)
        self.assertEqual(inv.status, "SCHEDULED")
        self.assertTrue(inv.invoice_number.startswith("INV-202604-test_inv-"))
        self.assertTrue(inv.provider_order_id.startswith("ord_"))

    def test_manual_invoice_is_payable_immediately_and_due_date_is_deadline(self):
        self.program.billing_mode = "INVOICE_REQUEST"
        self.program.save(update_fields=["billing_mode"])

        inv = invoice_service.create_for_next_period(self.program)

        self.assertEqual(inv.status, "PENDING")
        self.assertEqual(inv.due_date, inv.period_start + timedelta(days=15))

    def test_duplicate_invoice_prevented(self):
        """동일 기간 중복 인보이스 방지"""
        inv1 = invoice_service.create_for_next_period(self.program)
        self.assertIsNotNone(inv1)
        inv2 = invoice_service.create_for_next_period(self.program)
        self.assertIsNone(inv2)

    def test_no_expires_at_returns_none(self):
        self.program.subscription_expires_at = None
        self.program.save(update_fields=["subscription_expires_at"])
        inv = invoice_service.create_for_next_period(self.program)
        self.assertIsNone(inv)

    def test_create_rechecks_cancelled_program_under_lock(self):
        self.program.cancel_at_period_end = True
        self.program.save(update_fields=["cancel_at_period_end"])

        inv = invoice_service.create_for_next_period(self.program)

        self.assertIsNone(inv)
        self.assertFalse(Invoice.objects.filter(tenant=self.tenant).exists())

    def test_create_rechecks_inactive_program_under_lock(self):
        self.program.is_active = False
        self.program.save(update_fields=["is_active"])

        inv = invoice_service.create_for_next_period(self.program)

        self.assertIsNone(inv)

    def test_exempt_tenant_returns_none(self):
        """exempt 테넌트는 인보이스 생성 안 됨"""
        with self.settings(BILLING_EXEMPT_TENANT_IDS={self.tenant.id}):
            inv = invoice_service.create_for_next_period(self.program)
            self.assertIsNone(inv)

    def test_contract_tenant_invoice_uses_explicit_vat_breakdown(self):
        self.tenant.code = "ymath"
        self.tenant.save(update_fields=["code"])
        self.program.monthly_price = 150_000
        self.program.save(update_fields=["monthly_price"])

        inv = invoice_service.create_for_next_period(self.program)

        self.assertEqual(inv.supply_amount, 150_000)
        self.assertEqual(inv.tax_amount, 15_000)
        self.assertEqual(inv.total_amount, 165_000)

    def test_contract_price_drift_fails_closed_without_invoice(self):
        self.tenant.code = "ymath"
        self.tenant.save(update_fields=["code"])
        Program.objects.filter(pk=self.program.pk).update(monthly_price=198_000)
        self.program.refresh_from_db()

        with self.assertRaises(BillingPriceIntegrityError):
            invoice_service.create_for_next_period(self.program)

        self.assertFalse(Invoice.objects.filter(tenant=self.tenant).exists())

    def test_contract_price_drift_is_blocking_in_read_only_audit(self):
        self.tenant.code = "ymath"
        self.tenant.save(update_fields=["code"])
        Program.objects.filter(pk=self.program.pk).update(monthly_price=198_000)
        output = StringIO()

        call_command("audit_billing_fields", tenant="ymath", stdout=output)

        audit = output.getvalue()
        self.assertIn("contract_price_mismatch", audit)
        self.assertIn("new invoice creation is blocked", audit)


class TestInvoiceTransitions(InvoiceServiceTestBase):

    def _create_invoice(self, status="SCHEDULED"):
        period_start = date.today() + timedelta(days=1)
        period_end = period_start + timedelta(days=29)
        return Invoice.objects.create(
            tenant=self.tenant,
            invoice_number=f"INV-TEST-{Invoice.objects.count():03d}",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=period_start,
            period_end=period_end,
            due_date=period_start,
            status=status,
        )

    def test_scheduled_to_pending(self):
        inv = self._create_invoice("SCHEDULED")
        result = invoice_service.transition_to_pending(inv.pk)
        self.assertEqual(result.status, "PENDING")

    def test_pending_to_paid(self):
        inv = self._create_invoice("PENDING")
        result = invoice_service.mark_paid(inv.pk)
        self.assertEqual(result.status, "PAID")
        self.assertIsNotNone(result.paid_at)
        # 구독 갱신 확인
        self.program.refresh_from_db()
        self.assertEqual(self.program.subscription_expires_at, inv.period_end)

    def test_historical_receivable_payment_does_not_reactivate_subscription(self):
        self.program.subscription_status = "expired"
        original_expiry = date.today() - timedelta(days=60)
        self.program.subscription_expires_at = original_expiry
        self.program.save(
            update_fields=["subscription_status", "subscription_expires_at"]
        )
        inv = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-HISTORICAL-001",
            plan="pro",
            billing_mode="INVOICE_REQUEST",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=date.today() - timedelta(days=45),
            period_end=date.today() - timedelta(days=15),
            due_date=date.today() - timedelta(days=30),
            status="PENDING",
        )

        invoice_service.mark_paid(inv.pk)

        inv.refresh_from_db()
        self.program.refresh_from_db()
        self.assertEqual(inv.status, "PAID")
        self.assertEqual(self.program.subscription_status, "expired")
        self.assertEqual(self.program.subscription_expires_at, original_expiry)

    def test_late_payment_does_not_regress_later_subscription_expiry(self):
        later_expiry = date.today() + timedelta(days=90)
        self.program.subscription_status = "active"
        self.program.subscription_expires_at = later_expiry
        self.program.next_billing_at = later_expiry
        self.program.save(
            update_fields=[
                "subscription_status",
                "subscription_expires_at",
                "next_billing_at",
            ]
        )
        invoice_end = date.today() + timedelta(days=30)
        inv = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-LATE-NO-REGRESS",
            plan="pro",
            billing_mode="INVOICE_REQUEST",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=date.today(),
            period_end=invoice_end,
            due_date=date.today(),
            status="PENDING",
        )

        invoice_service.mark_paid(inv.pk)

        self.program.refresh_from_db()
        self.assertEqual(self.program.subscription_expires_at, later_expiry)
        self.assertEqual(self.program.next_billing_at, later_expiry)

    def test_pending_to_failed(self):
        inv = self._create_invoice("PENDING")
        result = invoice_service.mark_failed(inv.pk, reason="카드 한도 초과")
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.attempt_count, 1)
        self.assertIsNotNone(result.next_retry_at)
        self.assertEqual(result.failure_reason, "카드 한도 초과")

    def test_failed_to_pending_retry(self):
        inv = self._create_invoice("FAILED")
        result = invoice_service.retry_pending(inv.pk)
        self.assertEqual(result.status, "PENDING")

    def test_failed_to_overdue(self):
        inv = self._create_invoice("FAILED")
        result = invoice_service.mark_overdue(inv.pk)
        self.assertEqual(result.status, "OVERDUE")

    def test_overdue_to_paid(self):
        """연체 후 수동 입금 확인"""
        inv = self._create_invoice("OVERDUE")
        result = invoice_service.mark_paid(inv.pk)
        self.assertEqual(result.status, "PAID")

    def test_void_invoice(self):
        inv = self._create_invoice("SCHEDULED")
        result = invoice_service.void(inv.pk, reason="테스트 무효")
        self.assertEqual(result.status, "VOID")

    def test_invalid_transition_raises(self):
        inv = self._create_invoice("PAID")
        with self.assertRaises(InvoiceTransitionError):
            invoice_service.mark_failed(inv.pk)

    def test_paid_is_terminal(self):
        inv = self._create_invoice("PAID")
        with self.assertRaises(InvoiceTransitionError):
            invoice_service.void(inv.pk)


class TestMarkPaidRenewsSubscription(InvoiceServiceTestBase):

    def test_manual_confirmation_persists_reconciliation_transaction(self):
        inv = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-MANUAL-001",
            plan="pro",
            billing_mode="INVOICE_REQUEST",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=date(2026, 4, 13),
            period_end=date(2026, 5, 12),
            due_date=date(2026, 4, 28),
            status="PENDING",
        )

        paid = invoice_service.confirm_manual_payment(inv.pk)

        transaction = PaymentTransaction.objects.get(invoice=inv)
        self.assertEqual(paid.status, "PAID")
        self.assertEqual(transaction.status, "SUCCESS")
        self.assertEqual(transaction.provider, "manual")
        self.assertEqual(transaction.payment_method, "manual")
        self.assertEqual(transaction.amount, inv.total_amount)
        self.assertEqual(transaction.provider_order_id, inv.provider_order_id)

    def test_manual_confirmation_rolls_back_when_reconciliation_write_fails(self):
        inv = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-MANUAL-ROLLBACK-001",
            plan="pro",
            billing_mode="INVOICE_REQUEST",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=date(2026, 4, 13),
            period_end=date(2026, 5, 12),
            due_date=date(2026, 4, 28),
            status="PENDING",
        )
        original_expiry = self.program.subscription_expires_at

        with patch(
            "apps.billing.services.invoice_service.PaymentTransaction.objects.create",
            side_effect=RuntimeError("reconciliation unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "reconciliation unavailable"):
                invoice_service.confirm_manual_payment(inv.pk)

        inv.refresh_from_db()
        self.program.refresh_from_db()
        self.assertEqual(inv.status, "PENDING")
        self.assertIsNone(inv.paid_at)
        self.assertEqual(self.program.subscription_expires_at, original_expiry)
        self.assertFalse(PaymentTransaction.objects.filter(invoice=inv).exists())

    def test_mark_paid_renews_subscription(self):
        """입금 확인 시 구독이 자동 갱신된다"""
        period_start = date.today() + timedelta(days=1)
        period_end = period_start + timedelta(days=29)
        inv = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-RENEW-001",
            plan="pro",
            billing_mode="INVOICE_REQUEST",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=period_start,
            period_end=period_end,
            due_date=period_start,
            status="PENDING",
        )

        invoice_service.mark_paid(inv.pk)

        self.program.refresh_from_db()
        self.assertEqual(self.program.subscription_status, "active")
        self.assertEqual(self.program.subscription_expires_at, period_end)

    def test_mark_paid_from_grace_restores_active(self):
        """grace 상태에서 입금 확인 시 active 복원"""
        self.program.subscription_status = "grace"
        self.program.save(update_fields=["subscription_status"])

        period_start = date.today() + timedelta(days=1)
        period_end = period_start + timedelta(days=29)
        inv = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-GRACE-001",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=period_start,
            period_end=period_end,
            due_date=period_start,
            status="PENDING",
        )

        invoice_service.mark_paid(inv.pk)

        self.program.refresh_from_db()
        self.assertEqual(self.program.subscription_status, "active")


class TestRetryExhaustion(InvoiceServiceTestBase):

    @override_settings(BILLING_RETRY_MAX_ATTEMPTS=3, BILLING_RETRY_INTERVAL_DAYS=3)
    def test_retry_count_and_exhaustion(self):
        inv = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-RETRY-001",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=date(2026, 4, 13),
            period_end=date(2026, 5, 12),
            due_date=date(2026, 4, 13),
            status="PENDING",
        )

        # 1차 실패
        inv = invoice_service.mark_failed(inv.pk, reason="fail1")
        self.assertEqual(inv.attempt_count, 1)
        self.assertIsNotNone(inv.next_retry_at)

        # 재시도
        inv = invoice_service.retry_pending(inv.pk)
        inv = invoice_service.mark_failed(inv.pk, reason="fail2")
        self.assertEqual(inv.attempt_count, 2)

        # 3차 실패 → 재시도 소진
        inv = invoice_service.retry_pending(inv.pk)
        inv = invoice_service.mark_failed(inv.pk, reason="fail3")
        self.assertEqual(inv.attempt_count, 3)
        self.assertIsNone(inv.next_retry_at)  # 재시도 소진
