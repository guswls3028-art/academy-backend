"""
InvoiceService — 인보이스 생성/상태 전이의 유일한 진입점.

상태 모델:
  SCHEDULED → PENDING → PAID (종단)
                     → FAILED → PENDING (재시도)
                             → OVERDUE → PAID (종단)
                                      → VOID (종단)
  SCHEDULED → VOID (종단)

규칙:
  - (tenant, period_start, period_end) unique constraint로 이중 청구 방지.
  - provider_order_id는 자동 생성 (UUID 기반, PG 전송용).
  - invoice_number는 사람이 보는 표시용 (INV-YYYYMM-{tenant_code}-NNN).
  - 모든 상태 전이는 select_for_update()로 보호.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.models import Invoice, PaymentTransaction

if TYPE_CHECKING:
    from apps.core.models.program import Program

logger = logging.getLogger(__name__)

VALID_TRANSITIONS: dict[str, set[str]] = {
    "SCHEDULED": {"PENDING", "VOID"},
    "PENDING": {"PAID", "FAILED", "VOID"},
    "FAILED": {"PENDING", "OVERDUE", "VOID"},
    "OVERDUE": {"PAID", "VOID"},
    # PAID, VOID = 종단 상태
}


class InvoiceTransitionError(Exception):
    pass


class BillingPriceIntegrityError(Exception):
    """Stored contract price does not match the canonical tenant contract."""


def _validate_transition(current: str, target: str) -> None:
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvoiceTransitionError(
            f"Invalid invoice transition: {current} → {target}"
        )


def _lock_invoice(invoice_id: int) -> Invoice:
    return Invoice.objects.select_for_update().get(pk=invoice_id)


def _lock_program_then_invoice(invoice_id: int):
    """Canonical billing lock order: Program -> Invoice."""
    from apps.core.models.program import Program

    tenant_id = Invoice.objects.filter(pk=invoice_id).values_list(
        "tenant_id", flat=True
    ).get()
    program = Program.objects.select_for_update().get(tenant_id=tenant_id)
    invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
    return program, invoice


def _generate_invoice_number(tenant_code: str, period_start: date) -> str:
    """
    표시용 인보이스 번호 생성. INV-YYYYMM-{tenant_code}-NNN.
    동시 호출 시 count 충돌 방지: UUID suffix 추가 후 DB unique에 의존.
    """
    ym = period_start.strftime("%Y%m")
    count = Invoice.objects.filter(
        invoice_number__startswith=f"INV-{ym}-{tenant_code}-"
    ).count()
    seq = count + 1
    candidate = f"INV-{ym}-{tenant_code}-{seq:03d}"
    # 이미 존재하면 seq 증가 (race condition 방어)
    while Invoice.objects.filter(invoice_number=candidate).exists():
        seq += 1
        candidate = f"INV-{ym}-{tenant_code}-{seq:03d}"
    return candidate


def resolve_monthly_amounts(program: "Program") -> dict[str, int]:
    """Resolve a charge snapshot and fail closed on contract-price drift."""
    from apps.core.models.program import Program

    tenant_code = program.tenant.code
    contract_price = Program.get_contract_monthly_price(tenant_code)
    if contract_price is not None and program.monthly_price != contract_price:
        raise BillingPriceIntegrityError(
            "Contract price mismatch: "
            f"tenant={tenant_code} stored={program.monthly_price} expected={contract_price}"
        )
    return Program.calculate_monthly_amounts(program.monthly_price)


# ──────────────────────────────────────────────
# 인보이스 생성
# ──────────────────────────────────────────────

@transaction.atomic
def create_for_next_period(program: "Program") -> Invoice | None:
    """
    다음 구독 기간 인보이스 생성.
    이미 동일 기간 인보이스가 있으면 None 반환 (중복 방지).
    """
    from apps.core.models.program import Program

    program = (
        Program.objects.select_for_update()
        .select_related("tenant")
        .get(pk=program.pk)
    )
    if program.tenant_id in settings.BILLING_EXEMPT_TENANT_IDS:
        logger.info("Skip invoice creation for exempt tenant %s", program.tenant_id)
        return None
    if (
        not program.tenant.is_active
        or not program.is_active
        or program.subscription_status not in ("active", "grace")
        or program.cancel_at_period_end
    ):
        logger.info(
            "Skip invoice creation for inactive/cancelled subscription tenant=%s",
            program.tenant_id,
        )
        return None

    current_expires = program.subscription_expires_at
    if not current_expires:
        logger.warning(
            "Cannot create invoice: no expires_at for program %s", program.pk
        )
        return None

    period_start = current_expires + timedelta(days=1)
    period_end = period_start + relativedelta(months=1) - timedelta(days=1)

    amounts = resolve_monthly_amounts(program)

    # 결제 모드에 따른 due_date 결정
    if program.billing_mode == "AUTO_CARD":
        due_date = period_start  # 자동결제는 시작일에 청구
    else:
        due_date = period_start + timedelta(days=15)  # 세금계산서는 15일 유예

    tenant_code = program.tenant.code if hasattr(program, "tenant") else "unknown"

    try:
        invoice = Invoice.objects.create(
            tenant_id=program.tenant_id,
            invoice_number=_generate_invoice_number(tenant_code, period_start),
            plan=program.plan,
            billing_mode=program.billing_mode,
            supply_amount=amounts["supply_amount"],
            tax_amount=amounts["tax_amount"],
            total_amount=amounts["total_amount"],
            period_start=period_start,
            period_end=period_end,
            due_date=due_date,
            # 수동 청구는 생성 즉시 입금 확인 가능해야 한다. due_date는
            # 납부 기한이며 결제 가능 시작일이 아니다.
            status=("SCHEDULED" if program.billing_mode == "AUTO_CARD" else "PENDING"),
        )
    except IntegrityError:
        # unique_invoice_per_period constraint 위반 = 이미 존재
        logger.info(
            "Invoice already exists for tenant=%s period=%s~%s",
            program.tenant_id, period_start, period_end,
        )
        return None

    logger.info(
        "Invoice created: %s tenant=%s period=%s~%s amount=%s",
        invoice.invoice_number, program.tenant_id, period_start, period_end, invoice.total_amount,
    )
    return invoice


# ──────────────────────────────────────────────
# 상태 전이
# ──────────────────────────────────────────────

@transaction.atomic
def transition_to_pending(invoice_id: int) -> Invoice:
    """SCHEDULED → PENDING"""
    program, invoice = _lock_program_then_invoice(invoice_id)
    _validate_transition(invoice.status, "PENDING")
    invoice.status = "PENDING"
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


@transaction.atomic
def mark_paid(invoice_id: int, *, paid_at: datetime | None = None) -> Invoice:
    """
    PENDING/OVERDUE → PAID.
    수동 입금 확인 또는 자동 결제 성공 시 호출.
    구독 갱신도 함께 처리.
    """
    from apps.billing.services import subscription_service

    program, invoice = _lock_program_then_invoice(invoice_id)
    _validate_transition(invoice.status, "PAID")

    invoice.status = "PAID"
    invoice.paid_at = paid_at or timezone.now()
    invoice.failure_reason = ""
    invoice.failed_at = None
    invoice.next_retry_at = None
    invoice.save(update_fields=[
        "status", "paid_at", "failure_reason", "failed_at", "next_retry_at", "updated_at",
    ])

    # 과거 채권의 뒤늦은 정산은 수납 사실만 기록한다. 이미 끝난 서비스
    # 기간을 active로 되살리려면 별도의 명시적 extend/renew가 필요하다.
    should_renew = (
        invoice.period_end >= timezone.localdate()
        and (
            program.subscription_expires_at is None
            or invoice.period_end > program.subscription_expires_at
        )
    )
    if should_renew:
        subscription_service.renew(
            program_id=program.pk,
            new_expires_at=invoice.period_end,
            next_billing_at=invoice.period_end,
        )
        subscription_result = "renewed"
    else:
        logger.info(
            "Invoice paid without subscription mutation: invoice=%s "
            "tenant=%s period_end=%s current_expiry=%s",
            invoice.invoice_number,
            invoice.tenant_id,
            invoice.period_end,
            program.subscription_expires_at,
        )
        subscription_result = "unchanged"

    logger.info(
        "Invoice paid: %s tenant=%s subscription=%s period_end=%s",
        invoice.invoice_number,
        invoice.tenant_id,
        subscription_result,
        invoice.period_end,
    )
    return invoice


@transaction.atomic
def confirm_manual_payment(
    invoice_id: int,
    *,
    paid_at: datetime | None = None,
) -> Invoice:
    """Atomically confirm manual payment and persist its reconciliation row."""
    _program, invoice = _lock_program_then_invoice(invoice_id)
    if invoice.status == "FAILED":
        _validate_transition(invoice.status, "PENDING")
        invoice.status = "PENDING"
        invoice.save(update_fields=["status", "updated_at"])
    elif invoice.status not in ("PENDING", "OVERDUE"):
        raise InvoiceTransitionError(
            f"Cannot confirm manual payment from status: {invoice.status}"
        )

    invoice = mark_paid(invoice.pk, paid_at=paid_at)
    PaymentTransaction.objects.create(
        tenant_id=invoice.tenant_id,
        invoice=invoice,
        amount=invoice.total_amount,
        status="SUCCESS",
        provider="manual",
        payment_method="manual",
        provider_order_id=invoice.provider_order_id,
        idempotency_key=f"manual_{invoice.provider_order_id}",
        processed_at=invoice.paid_at,
    )
    return invoice


@transaction.atomic
def mark_failed(invoice_id: int, *, reason: str = "") -> Invoice:
    """
    PENDING → FAILED.
    결제 실패 시 호출. 재시도 스케줄링.
    """
    _program, invoice = _lock_program_then_invoice(invoice_id)
    _validate_transition(invoice.status, "FAILED")

    invoice.status = "FAILED"
    invoice.failed_at = timezone.now()
    invoice.failure_reason = reason
    invoice.attempt_count += 1

    max_attempts = settings.BILLING_RETRY_MAX_ATTEMPTS
    retry_interval = settings.BILLING_RETRY_INTERVAL_DAYS

    if invoice.attempt_count < max_attempts:
        invoice.next_retry_at = timezone.localdate() + timedelta(days=retry_interval)
    else:
        invoice.next_retry_at = None  # 재시도 소진

    invoice.save(update_fields=[
        "status", "failed_at", "failure_reason",
        "attempt_count", "next_retry_at", "updated_at",
    ])

    logger.warning(
        "Invoice failed: %s tenant=%s attempt=%d/%d reason=%s next_retry=%s",
        invoice.invoice_number, invoice.tenant_id,
        invoice.attempt_count, max_attempts, reason, invoice.next_retry_at,
    )
    return invoice


@transaction.atomic
def mark_overdue(invoice_id: int) -> Invoice:
    """FAILED → OVERDUE. 재시도 소진 후 연체 전환."""
    _program, invoice = _lock_program_then_invoice(invoice_id)
    _validate_transition(invoice.status, "OVERDUE")
    invoice.status = "OVERDUE"
    invoice.save(update_fields=["status", "updated_at"])

    logger.warning(
        "Invoice overdue: %s tenant=%s", invoice.invoice_number, invoice.tenant_id,
    )
    return invoice


@transaction.atomic
def void(invoice_id: int, *, reason: str = "") -> Invoice:
    """SCHEDULED/FAILED/OVERDUE → VOID. 무효 처리."""
    _program, invoice = _lock_program_then_invoice(invoice_id)
    _validate_transition(invoice.status, "VOID")
    invoice.status = "VOID"
    invoice.memo = reason if reason else invoice.memo
    invoice.save(update_fields=["status", "memo", "updated_at"])

    logger.info("Invoice voided: %s reason=%s", invoice.invoice_number, reason)
    return invoice


@transaction.atomic
def retry_pending(invoice_id: int) -> Invoice:
    """FAILED → PENDING. 재시도 전 PENDING 상태로 복귀."""
    _program, invoice = _lock_program_then_invoice(invoice_id)
    _validate_transition(invoice.status, "PENDING")
    invoice.status = "PENDING"
    invoice.save(update_fields=["status", "updated_at"])
    return invoice
