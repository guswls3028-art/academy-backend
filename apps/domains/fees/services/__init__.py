# PATH: apps/domains/fees/services.py
"""
수납 관리 서비스 레이어.

핵심 원칙:
- 데이터 정합성 최우선 (select_for_update, atomic)
- 테넌트 격리 절대
- paid_amount 비정규화 필드는 항상 FeePayment SUM으로 재계산
"""

import logging
from datetime import date, timedelta
from itertools import groupby
from operator import attrgetter

from django.db import IntegrityError, transaction
from django.db.models import Sum, Q
from django.utils import timezone

from ..models import (
    FeeTemplate,
    StudentFee,
    StudentInvoice,
    InvoiceItem,
    FeePayment,
)

logger = logging.getLogger(__name__)


# ========================================================
# 청구서 번호 생성
# ========================================================

def _next_invoice_number(tenant, year: int, month: int) -> str:
    """
    FEE-{YYYY}-{MM}-{NNNN} 형식의 청구번호 생성.
    해당 테넌트+연월 내에서 순번 증가.

    반드시 transaction.atomic() 내부에서 호출해야 한다.
    select_for_update()로 해당 연월의 마지막 행을 잠가서
    동시 호출 시 중복 번호 생성을 방지한다.
    """
    prefix = f"FEE-{year}-{month:02d}-"
    # select_for_update: 해당 연월의 모든 invoice를 잠금으로써
    # 동시 트랜잭션이 같은 번호를 읽는 것을 방지한다.
    last_invoice = (
        StudentInvoice.objects
        .select_for_update()
        .filter(tenant=tenant, invoice_number__startswith=prefix)
        .order_by("-invoice_number")
        .only("invoice_number")
        .first()
    )
    if last_invoice:
        try:
            seq = int(last_invoice.invoice_number.split("-")[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


# ========================================================
# 월 청구서 일괄 생성
# ========================================================

def generate_monthly_invoices(
    tenant,
    billing_year: int,
    billing_month: int,
    due_date: date,
    created_by=None,
) -> dict:
    """
    해당 월의 청구서를 일괄 생성한다.

    1. 활성 StudentFee (MONTHLY) 조회
    2. 학생별 그룹핑
    3. 학생별로 StudentInvoice + InvoiceItem 생성
    4. 이미 존재하는 청구서(동일 학생+연월)는 skip

    Returns:
        {"created": int, "skipped": int, "errors": list[str]}
    """
    billing_period = f"{billing_year}-{billing_month:02d}"

    # 활성 StudentFee 중 해당 월에 유효한 것만 조회
    # (MONTHLY + ONE_TIME 모두 포함 — ONE_TIME은 아래에서 중복 청구 방지)
    all_student_fees = (
        StudentFee.objects
        .filter(
            tenant=tenant,
            is_active=True,
            fee_template__is_active=True,
        )
        .select_related("student", "fee_template")
        .order_by("student_id")
    )

    # 유효 기간 + ONE_TIME 중복 필터링
    student_fees = []
    # cancel된 invoice는 제외 — cancel 후 재청구 가능해야 한다.
    one_time_already_billed = set(
        InvoiceItem.objects
        .filter(
            tenant=tenant,
            fee_template__billing_cycle=FeeTemplate.BillingCycle.ONE_TIME,
            invoice__status__in=["PENDING", "PARTIAL", "PAID", "OVERDUE"],
        )
        .values_list("fee_template_id", "invoice__student_id")
    )

    one_time_dup_skipped = []  # silent skip 대신 errors로 보고할 수 있게 추적
    for sf in all_student_fees:
        # 유효 기간 체크
        if sf.billing_start_month and billing_period < sf.billing_start_month:
            continue
        if sf.billing_end_month and billing_period > sf.billing_end_month:
            continue

        # ONE_TIME 중복 방지: 이미 청구된 적 있으면 skip + 보고
        if sf.fee_template.billing_cycle == FeeTemplate.BillingCycle.ONE_TIME:
            if (sf.fee_template_id, sf.student_id) in one_time_already_billed:
                one_time_dup_skipped.append(
                    f"{sf.student.name}: {sf.fee_template.name} (1회성, 이미 청구됨)"
                )
                continue

        student_fees.append(sf)

    result = {"created": 0, "skipped": 0, "errors": list(one_time_dup_skipped)}

    # 이미 청구서가 있는 학생 목록
    existing_student_ids = set(
        StudentInvoice.objects
        .filter(
            tenant=tenant,
            billing_year=billing_year,
            billing_month=billing_month,
        )
        .exclude(status="CANCELLED")
        .values_list("student_id", flat=True)
    )

    # 학생별 그룹핑
    for student_id, fees_iter in groupby(student_fees, key=attrgetter("student_id")):
        fees = list(fees_iter)

        if student_id in existing_student_ids:
            result["skipped"] += 1
            continue

        student_name = fees[0].student.name if fees else "?"
        # invoice_number race 시 1회 재시도 — 동시 트랜잭션이 같은 seq를 읽는 경우 IntegrityError → retry.
        for attempt in range(2):
            try:
                with transaction.atomic():
                    student = fees[0].student
                    total = sum(sf.effective_amount for sf in fees)

                    if total == 0:
                        result["skipped"] += 1
                        break

                    inv_number = _next_invoice_number(tenant, billing_year, billing_month)
                    invoice = StudentInvoice.objects.create(
                        tenant=tenant,
                        student=student,
                        invoice_number=inv_number,
                        billing_year=billing_year,
                        billing_month=billing_month,
                        total_amount=total,
                        due_date=due_date,
                        created_by=created_by,
                    )

                    items = []
                    for sf in fees:
                        items.append(InvoiceItem(
                            tenant=tenant,
                            invoice=invoice,
                            fee_template=sf.fee_template,
                            description=sf.fee_template.name,
                            amount=sf.effective_amount,
                        ))
                    InvoiceItem.objects.bulk_create(items)

                    result["created"] += 1
                    break

            except IntegrityError as e:
                if attempt == 0:
                    logger.warning("Invoice number race for student %s, retrying: %s", student_name, e)
                    continue
                logger.exception("Invoice generation IntegrityError after retry for %s: %s", student_name, e)
                result["errors"].append(f"{student_name}: invoice 번호 충돌(재시도 실패)")
            except Exception as e:
                logger.exception("Invoice generation failed for student %s: %s", student_name, e)
                result["errors"].append(f"{student_name}: {str(e)}")
                break

    return result


# ========================================================
# 수납 기록
# ========================================================

PAYMENT_DEDUP_WINDOW_SECONDS = 60  # 동일 파라미터 중복 납부 방지 윈도우


@transaction.atomic
def record_payment(
    tenant,
    invoice_id: int,
    amount: int,
    payment_method: str,
    paid_at=None,
    recorded_by=None,
    receipt_note: str = "",
    memo: str = "",
    idempotency_key: str = "",
) -> FeePayment:
    """
    청구서에 대한 납부를 기록한다.

    1. select_for_update로 invoice 잠금
    2. 멱등성 검사 (idempotency_key 또는 시간 윈도우 기반)
    3. amount <= outstanding 검증
    4. FeePayment 생성
    5. invoice.paid_amount 재계산
    6. 완납 시 status=PAID
    7. messaging trigger 호출

    idempotency_key: 클라이언트가 제공하는 중복 요청 방지 키.
        동일 키로 이미 성공한 납부가 있으면 기존 납부를 반환한다.
        키가 없어도 동일 invoice+금액+수단의 짧은 시간 내 중복을 방지한다.
    """
    invoice = (
        StudentInvoice.objects
        .select_for_update()
        .get(id=invoice_id, tenant=tenant)
    )

    if invoice.status == "CANCELLED":
        raise ValueError("취소된 청구서에는 수납을 기록할 수 없습니다.")

    # --- 멱등성 검사: idempotency_key 기반 (있으면 시간 윈도우 검사 skip) ---
    if idempotency_key:
        existing = FeePayment.objects.filter(
            tenant=tenant,
            invoice=invoice,
            idempotency_key=idempotency_key,
            status="SUCCESS",
        ).first()
        if existing:
            logger.info(
                "Duplicate payment blocked by idempotency_key=%s (existing payment %d)",
                idempotency_key, existing.id,
            )
            return existing
        now = timezone.now()
    else:
        # --- 멱등성 검사: 시간 윈도우 기반 (더블클릭/재전송 방지) ---
        # idempotency_key 미제공 시에만 적용 — 의도적 분할 납부는 key를 반드시 보내야 함.
        now = timezone.now()
        dedup_cutoff = now - timedelta(seconds=PAYMENT_DEDUP_WINDOW_SECONDS)
        recent_duplicate = FeePayment.objects.filter(
            tenant=tenant,
            invoice=invoice,
            amount=amount,
            payment_method=payment_method,
            status="SUCCESS",
            created_at__gte=dedup_cutoff,
        ).exists()
        if recent_duplicate:
            raise ValueError(
                f"동일한 납부({amount:,}원, {payment_method})가 "
                f"{PAYMENT_DEDUP_WINDOW_SECONDS}초 이내에 이미 기록되었습니다. "
                f"중복 납부면 잠시 후 다시 시도하거나, 의도적 분할 납부는 idempotency_key를 지정하세요."
            )

    outstanding = invoice.outstanding_amount
    if amount > outstanding:
        raise ValueError(
            f"납부 금액({amount:,}원)이 미납 잔액({outstanding:,}원)을 초과합니다."
        )

    if paid_at is None:
        paid_at = now

    payment = FeePayment.objects.create(
        tenant=tenant,
        invoice=invoice,
        student=invoice.student,
        amount=amount,
        payment_method=payment_method,
        status="SUCCESS",
        paid_at=paid_at,
        recorded_by=recorded_by,
        receipt_note=receipt_note,
        memo=memo,
        idempotency_key=idempotency_key,
    )

    _recalculate_invoice(invoice)

    # 완납 시 메시징 트리거
    if invoice.status == "PAID":
        transaction.on_commit(lambda: _send_payment_complete_notification(
            tenant, invoice.student, billing_month=invoice.billing_month, amount=amount,
        ))

    return payment


# ========================================================
# 수납 취소
# ========================================================

@transaction.atomic
def cancel_payment(tenant, payment_id: int) -> FeePayment:
    """
    수납 기록을 취소한다.
    invoice.paid_amount 재계산.
    """
    payment = (
        FeePayment.objects
        .select_related("invoice")
        .get(id=payment_id, tenant=tenant)
    )

    if payment.status != "SUCCESS":
        raise ValueError("이미 취소/환불된 수납 기록입니다.")

    payment.status = "CANCELLED"
    payment.save(update_fields=["status", "updated_at"])

    invoice = (
        StudentInvoice.objects
        .select_for_update()
        .get(id=payment.invoice_id, tenant=tenant)
    )
    _recalculate_invoice(invoice)

    return payment


# ========================================================
# 청구서 취소
# ========================================================

@transaction.atomic
def cancel_invoice(tenant, invoice_id: int) -> StudentInvoice:
    """청구서를 취소한다. 성공한 수납이 있으면 취소 불가."""
    invoice = (
        StudentInvoice.objects
        .select_for_update()
        .get(id=invoice_id, tenant=tenant)
    )

    has_active_payments = invoice.payments.filter(status="SUCCESS").exists()
    if has_active_payments:
        raise ValueError("수납 기록이 있는 청구서는 취소할 수 없습니다. 수납을 먼저 취소하세요.")

    invoice.status = "CANCELLED"
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


# ========================================================
# 연체 처리
# ========================================================

def mark_overdue_invoices(tenant=None):
    """
    납부기한 경과한 미납/부분납 청구서를 연체로 변경.
    tenant=None이면 전체 테넌트 대상 (스케줄 태스크용).
    """
    qs = StudentInvoice.objects.filter(
        status__in=["PENDING", "PARTIAL"],
        due_date__lt=timezone.localdate(),
    )
    if tenant:
        qs = qs.filter(tenant=tenant)

    updated = qs.update(status="OVERDUE")
    logger.info("Marked %d invoices as overdue", updated)
    return updated


# ========================================================
# 수강 등록 시 자동 비용 할당
# ========================================================

def auto_assign_fees_on_enrollment(tenant, student, lecture, enrollment):
    """
    강의에 연결된 활성 FeeTemplate이 있으면 StudentFee를 자동 생성.
    이미 동일 비목이 할당되어 있으면 skip.
    """
    templates = FeeTemplate.objects.filter(
        tenant=tenant,
        lecture=lecture,
        is_active=True,
        auto_assign=True,
    )

    created_count = 0
    for tmpl in templates:
        _, created = StudentFee.objects.get_or_create(
            tenant=tenant,
            student=student,
            fee_template=tmpl,
            defaults={
                "enrollment": enrollment,
                "is_active": True,
            },
        )
        if created:
            created_count += 1

    return created_count


# ========================================================
# 대시보드 통계
# ========================================================

def get_dashboard_stats(tenant, year: int, month: int) -> dict:
    """이번 달 수납 현황 요약."""
    invoices = StudentInvoice.objects.filter(
        tenant=tenant,
        billing_year=year,
        billing_month=month,
    ).exclude(status="CANCELLED")

    agg = invoices.aggregate(
        total_billed=Sum("total_amount"),
        total_paid=Sum("paid_amount"),
    )

    total_billed = agg["total_billed"] or 0
    total_paid = agg["total_paid"] or 0

    overdue_count = invoices.filter(status="OVERDUE").count()
    pending_count = invoices.filter(status__in=["PENDING", "PARTIAL"]).count()
    paid_count = invoices.filter(status="PAID").count()

    # 비목 유형별 통계
    from ..models import InvoiceItem
    fee_type_stats = list(
        InvoiceItem.objects
        .filter(
            tenant=tenant,
            invoice__billing_year=year,
            invoice__billing_month=month,
        )
        .exclude(invoice__status="CANCELLED")
        .values("fee_template__fee_type")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )

    return {
        "billing_year": year,
        "billing_month": month,
        "total_billed": total_billed,
        "total_paid": total_paid,
        "total_outstanding": total_billed - total_paid,
        "overdue_count": overdue_count,
        "pending_count": pending_count,
        "paid_count": paid_count,
        "invoice_count": invoices.count(),
        "by_fee_type": [
            {"fee_type": s["fee_template__fee_type"] or "OTHER", "total": s["total"]}
            for s in fee_type_stats
        ],
    }


# ========================================================
# Internal helpers
# ========================================================

def _recalculate_invoice(invoice: StudentInvoice):
    """
    FeePayment 합계로 paid_amount 재계산 + 상태 업데이트.
    호출 전에 invoice가 select_for_update 된 상태여야 한다.
    """
    paid_sum = (
        invoice.payments
        .filter(status="SUCCESS")
        .aggregate(total=Sum("amount"))["total"]
    ) or 0

    invoice.paid_amount = paid_sum

    if paid_sum >= invoice.total_amount:
        invoice.status = "PAID"
        if not invoice.paid_at:
            invoice.paid_at = timezone.now()
    elif paid_sum > 0:
        invoice.status = "PARTIAL"
        invoice.paid_at = None
    else:
        # 납부 없음 → 연체 상태는 유지, 아니면 PENDING
        if invoice.status != "OVERDUE":
            invoice.status = "PENDING"
        invoice.paid_at = None

    invoice.save(update_fields=["paid_amount", "status", "paid_at", "updated_at"])


def _send_payment_complete_notification(tenant, student, billing_month: int, amount: int):
    """수납 완료 알림 발송 (messaging 연동)."""
    try:
        from apps.domains.messaging.services import send_event_notification
        send_event_notification(
            tenant=tenant,
            trigger="payment_complete",
            student=student,
            send_to="parent",
            context={
                "강의명": "-",
                "차시명": "-",
                "납부금액": f"{amount:,}원",
                "청구월": f"{billing_month}월",
                "_domain_object_id": f"payment_{student.id}_{billing_month}",
            },
        )
    except Exception:
        logger.exception("Payment complete notification failed for student %s", student.id)
