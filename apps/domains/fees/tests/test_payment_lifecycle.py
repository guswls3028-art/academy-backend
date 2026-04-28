"""수납 도메인 핵심 라이프사이클 단위테스트.

검증 범위:
- 부분납 → 추가납 → 완납 시 paid_amount/status 정합성
- 환불(cancel_payment) 시 paid_amount 차감 + status 되돌림
- idempotency_key 중복 호출 시 동일 payment 반환
- 시간 윈도우 중복 차단
- 초과 납부 / 취소 청구서 / 활성 수납 invoice 취소 등 가드
- mark_overdue_invoices 정확성
- 테넌트 격리 (cross-tenant payment 시도 차단)
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.fees.models import (
    FeePayment,
    FeeTemplate,
    StudentInvoice,
    InvoiceItem,
)
from apps.domains.fees.services import (
    cancel_invoice,
    cancel_payment,
    mark_overdue_invoices,
    record_payment,
)
from apps.domains.students.models import Student

User = get_user_model()


class FeesTestMixin:
    """fees 도메인용 fixture helper."""

    def make_tenant(self, code="t_fees"):
        return Tenant.objects.create(code=code, name=f"Academy {code}")

    def make_student(self, tenant, suffix=1):
        user = User.objects.create_user(
            username=f"student_fee_{tenant.code}_{suffix}",
            password="test1234",
        )
        return Student.objects.create(
            tenant=tenant, user=user,
            ps_number=f"PS{suffix:04d}",
            omr_code=f"OMR{suffix:04d}"[:8],
            name=f"학생{suffix}",
            parent_phone=f"010-0000-{suffix:04d}"[:13],
        )

    def make_fee_template(self, tenant, amount=100_000):
        return FeeTemplate.objects.create(
            tenant=tenant,
            name=f"수강료-{tenant.code}",
            fee_type=FeeTemplate.FeeType.TUITION,
            amount=amount,
        )

    def make_invoice(self, tenant, student, total=100_000, year=2026, month=4):
        invoice = StudentInvoice.objects.create(
            tenant=tenant,
            student=student,
            invoice_number=f"FEE-{year}-{month:02d}-{student.id:04d}",
            billing_year=year,
            billing_month=month,
            total_amount=total,
            due_date=timezone.localdate() + timedelta(days=10),
        )
        InvoiceItem.objects.create(
            tenant=tenant,
            invoice=invoice,
            description="수강료",
            amount=total,
        )
        return invoice


class PaymentLifecycleTest(FeesTestMixin, TestCase):
    """부분납 → 완납 → 환불 흐름 정합성."""

    def setUp(self):
        self.tenant = self.make_tenant()
        self.student = self.make_student(self.tenant)
        self.invoice = self.make_invoice(self.tenant, self.student, total=100_000)

    def test_partial_then_complete_updates_status(self):
        # 첫 부분납 30,000원
        record_payment(
            self.tenant, self.invoice.id, 30_000, "CASH",
            idempotency_key="key-1",
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, 30_000)
        self.assertEqual(self.invoice.status, "PARTIAL")
        self.assertIsNone(self.invoice.paid_at)

        # 추가납 50,000원
        record_payment(
            self.tenant, self.invoice.id, 50_000, "BANK_TRANSFER",
            idempotency_key="key-2",
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, 80_000)
        self.assertEqual(self.invoice.status, "PARTIAL")

        # 완납 20,000원
        record_payment(
            self.tenant, self.invoice.id, 20_000, "CASH",
            idempotency_key="key-3",
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, 100_000)
        self.assertEqual(self.invoice.status, "PAID")
        self.assertIsNotNone(self.invoice.paid_at)

    def test_overpayment_blocked(self):
        record_payment(
            self.tenant, self.invoice.id, 60_000, "CASH",
            idempotency_key="part-1",
        )
        # 잔액 40,000원인데 50,000원 납부 시도
        with self.assertRaises(ValueError) as ctx:
            record_payment(
                self.tenant, self.invoice.id, 50_000, "CASH",
                idempotency_key="part-2",
            )
        self.assertIn("미납 잔액", str(ctx.exception))

    def test_cancel_payment_restores_status(self):
        # 완납 후 환불(취소) → PARTIAL or PENDING으로 되돌림
        p1 = record_payment(
            self.tenant, self.invoice.id, 60_000, "CASH",
            idempotency_key="r1",
        )
        record_payment(
            self.tenant, self.invoice.id, 40_000, "CASH",
            idempotency_key="r2",
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PAID")

        # p1 취소 → paid_amount 60_000 차감
        cancel_payment(self.tenant, p1.id)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, 40_000)
        self.assertEqual(self.invoice.status, "PARTIAL")
        self.assertIsNone(self.invoice.paid_at)

        # p1 다시 취소 시도 → ValueError
        with self.assertRaises(ValueError):
            cancel_payment(self.tenant, p1.id)


class IdempotencyTest(FeesTestMixin, TestCase):
    """idempotency_key + 시간 윈도우 중복 방지."""

    def setUp(self):
        self.tenant = self.make_tenant(code="t_idem")
        self.student = self.make_student(self.tenant)
        self.invoice = self.make_invoice(self.tenant, self.student, total=100_000)

    def test_idempotency_key_returns_same_payment(self):
        p1 = record_payment(
            self.tenant, self.invoice.id, 50_000, "CASH",
            idempotency_key="dup-key",
        )
        # 동일 키로 재호출 → 새로 생성하지 않고 기존 반환
        p2 = record_payment(
            self.tenant, self.invoice.id, 50_000, "CASH",
            idempotency_key="dup-key",
        )
        self.assertEqual(p1.id, p2.id)
        self.assertEqual(
            FeePayment.objects.filter(invoice=self.invoice).count(), 1
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, 50_000)

    def test_window_dedup_blocks_recent_duplicate(self):
        # 키 없이 동일 invoice/금액/수단 빠른 중복 → ValueError
        record_payment(
            self.tenant, self.invoice.id, 30_000, "CASH",
        )
        with self.assertRaises(ValueError) as ctx:
            record_payment(
                self.tenant, self.invoice.id, 30_000, "CASH",
            )
        self.assertIn("중복", str(ctx.exception))

    def test_idempotency_constraint_prevents_db_duplicate(self):
        # DB 레벨 partial unique constraint도 살아 있는지 검증.
        record_payment(
            self.tenant, self.invoice.id, 50_000, "CASH",
            idempotency_key="hard-key",
        )
        # 직접 INSERT로 동일 key 재시도 → IntegrityError
        with self.assertRaises(IntegrityError):
            FeePayment.objects.create(
                tenant=self.tenant,
                invoice=self.invoice,
                student=self.student,
                amount=10_000,
                payment_method="CASH",
                paid_at=timezone.now(),
                idempotency_key="hard-key",
            )


class InvoiceCancelGuardTest(FeesTestMixin, TestCase):
    """청구서 취소 가드: 활성 수납 있으면 취소 불가."""

    def setUp(self):
        self.tenant = self.make_tenant(code="t_cancel")
        self.student = self.make_student(self.tenant)
        self.invoice = self.make_invoice(self.tenant, self.student, total=50_000)

    def test_cancel_invoice_with_active_payment_raises(self):
        record_payment(
            self.tenant, self.invoice.id, 20_000, "CASH",
            idempotency_key="any",
        )
        with self.assertRaises(ValueError) as ctx:
            cancel_invoice(self.tenant, self.invoice.id)
        self.assertIn("수납을 먼저 취소", str(ctx.exception))

    def test_cancel_invoice_without_payment_succeeds(self):
        cancel_invoice(self.tenant, self.invoice.id)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "CANCELLED")

    def test_payment_blocked_on_cancelled_invoice(self):
        cancel_invoice(self.tenant, self.invoice.id)
        with self.assertRaises(ValueError):
            record_payment(
                self.tenant, self.invoice.id, 10_000, "CASH",
                idempotency_key="cancelled",
            )


class OverdueTest(FeesTestMixin, TestCase):
    """납부 기한 경과 청구서 → OVERDUE 일괄 처리."""

    def test_mark_overdue_only_pending_or_partial(self):
        tenant = self.make_tenant(code="t_overdue")
        student = self.make_student(tenant)
        past = timezone.localdate() - timedelta(days=5)
        future = timezone.localdate() + timedelta(days=5)

        # 미납·기한 경과 → OVERDUE 대상
        inv_overdue = self.make_invoice(tenant, student, year=2026, month=1)
        StudentInvoice.objects.filter(id=inv_overdue.id).update(due_date=past)

        # 부분납·기한 경과 → OVERDUE 대상
        inv_partial = self.make_invoice(tenant, student, year=2026, month=2)
        StudentInvoice.objects.filter(id=inv_partial.id).update(due_date=past)
        record_payment(
            tenant, inv_partial.id, 30_000, "CASH",
            idempotency_key="part-overdue",
        )

        # 기한 미도래 → 제외
        inv_future = self.make_invoice(tenant, student, year=2026, month=3)
        StudentInvoice.objects.filter(id=inv_future.id).update(due_date=future)

        # 완납 → 제외
        inv_paid = self.make_invoice(tenant, student, total=10_000, year=2026, month=5)
        StudentInvoice.objects.filter(id=inv_paid.id).update(due_date=past)
        record_payment(
            tenant, inv_paid.id, 10_000, "CASH",
            idempotency_key="paid-overdue",
        )

        updated = mark_overdue_invoices(tenant)
        self.assertEqual(updated, 2)

        inv_overdue.refresh_from_db()
        inv_partial.refresh_from_db()
        inv_future.refresh_from_db()
        inv_paid.refresh_from_db()
        self.assertEqual(inv_overdue.status, "OVERDUE")
        self.assertEqual(inv_partial.status, "OVERDUE")
        self.assertEqual(inv_future.status, "PENDING")
        self.assertEqual(inv_paid.status, "PAID")


class TenantIsolationTest(FeesTestMixin, TestCase):
    """수납이 절대 cross-tenant로 새지 않도록."""

    def test_record_payment_blocks_other_tenant(self):
        t_a = self.make_tenant(code="t_a")
        t_b = self.make_tenant(code="t_b")
        student_a = self.make_student(t_a, suffix=1)
        invoice_a = self.make_invoice(t_a, student_a, total=50_000)

        # t_b 컨텍스트로 t_a invoice를 결제 시도 → DoesNotExist
        with self.assertRaises(StudentInvoice.DoesNotExist):
            record_payment(
                t_b, invoice_a.id, 10_000, "CASH",
                idempotency_key="cross",
            )

    def test_cancel_payment_blocks_other_tenant(self):
        t_a = self.make_tenant(code="t_a2")
        t_b = self.make_tenant(code="t_b2")
        student_a = self.make_student(t_a, suffix=1)
        invoice_a = self.make_invoice(t_a, student_a, total=50_000)
        payment = record_payment(
            t_a, invoice_a.id, 10_000, "CASH",
            idempotency_key="key",
        )
        with self.assertRaises(FeePayment.DoesNotExist):
            cancel_payment(t_b, payment.id)
