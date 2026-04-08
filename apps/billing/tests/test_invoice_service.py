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

from django.test import TestCase, override_settings

from apps.billing.models import Invoice
from apps.billing.services import invoice_service
from apps.billing.services.invoice_service import InvoiceTransitionError
from apps.core.models.program import Program
from apps.core.models.tenant import Tenant


class InvoiceServiceTestBase(TestCase):

    def setUp(self):
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

    def test_exempt_tenant_returns_none(self):
        """exempt 테넌트는 인보이스 생성 안 됨"""
        with self.settings(BILLING_EXEMPT_TENANT_IDS={self.tenant.id}):
            inv = invoice_service.create_for_next_period(self.program)
            self.assertIsNone(inv)


class TestInvoiceTransitions(InvoiceServiceTestBase):

    def _create_invoice(self, status="SCHEDULED"):
        return Invoice.objects.create(
            tenant=self.tenant,
            invoice_number=f"INV-TEST-{Invoice.objects.count():03d}",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=date(2026, 4, 13),
            period_end=date(2026, 5, 12),
            due_date=date(2026, 4, 13),
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
        self.assertEqual(self.program.subscription_expires_at, date(2026, 5, 12))

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

    def test_mark_paid_renews_subscription(self):
        """입금 확인 시 구독이 자동 갱신된다"""
        inv = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-RENEW-001",
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

        invoice_service.mark_paid(inv.pk)

        self.program.refresh_from_db()
        self.assertEqual(self.program.subscription_status, "active")
        self.assertEqual(self.program.subscription_expires_at, date(2026, 5, 12))

    def test_mark_paid_from_grace_restores_active(self):
        """grace 상태에서 입금 확인 시 active 복원"""
        self.program.subscription_status = "grace"
        self.program.save(update_fields=["subscription_status"])

        inv = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-GRACE-001",
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
