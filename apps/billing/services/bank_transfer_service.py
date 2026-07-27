"""계좌이체 청구, 입금 확인, 세금계산서 발행 대기열 서비스."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.billing.models import (
    BankTransferNotice,
    BusinessProfile,
    Invoice,
    TaxInvoiceIssue,
)
from apps.billing.services import invoice_service
from apps.core.models.program import Program


class BankTransferError(Exception):
    """계좌이체 워크플로우 도메인 오류."""


class BankTransferUnavailable(BankTransferError):
    """운영 계좌가 아직 활성화되지 않음."""


class BankTransferConflict(BankTransferError):
    """현재 청구 상태와 요청이 충돌함."""


def get_bank_account() -> dict[str, str | bool]:
    enabled = bool(settings.BILLING_BANK_TRANSFER_ENABLED)
    return {
        "enabled": enabled,
        "bank_name": settings.BILLING_BANK_NAME if enabled else "",
        "account_number": (
            settings.BILLING_BANK_ACCOUNT_NUMBER if enabled else ""
        ),
        "account_holder": (
            settings.BILLING_BANK_ACCOUNT_HOLDER if enabled else ""
        ),
    }


def _require_available() -> None:
    bank = get_bank_account()
    if not bank["enabled"] or not all(
        (
            bank["bank_name"],
            bank["account_number"],
            bank["account_holder"],
        )
    ):
        raise BankTransferUnavailable(
            "계좌이체 결제를 준비 중입니다. 운영자에게 문의해 주세요."
        )


def _lock_notice_workflow(
    notice_id: int,
) -> tuple[Program, Invoice, BankTransferNotice]:
    """Canonical lock order for review actions: Program -> Invoice -> Notice."""
    seed = BankTransferNotice.objects.filter(pk=notice_id).values(
        "invoice_id",
    ).first()
    if seed is None:
        raise BankTransferNotice.DoesNotExist
    invoice_tenant_id = Invoice.objects.filter(
        pk=seed["invoice_id"]
    ).values_list("tenant_id", flat=True).get()
    program = Program.objects.select_for_update().get(
        tenant_id=invoice_tenant_id
    )
    invoice = Invoice.objects.select_for_update().get(
        pk=seed["invoice_id"]
    )
    notice = BankTransferNotice.objects.select_for_update().get(pk=notice_id)
    return program, invoice, notice


@transaction.atomic
def activate_for_tenant(tenant_id: int) -> Invoice:
    """카드 자동결제 예정 청구를 안전하게 계좌이체 청구로 전환한다."""
    _require_available()
    if tenant_id in settings.BILLING_EXEMPT_TENANT_IDS:
        raise BankTransferConflict("결제 제외 테넌트에는 청구서를 만들 수 없습니다.")

    program = (
        Program.objects.select_for_update()
        .select_related("tenant")
        .get(tenant_id=tenant_id)
    )
    blocking = (
        Invoice.objects.select_for_update()
        .filter(
            tenant_id=tenant_id,
            billing_mode="AUTO_CARD",
            status__in=("PENDING", "FAILED", "OVERDUE"),
        )
        .first()
    )
    if blocking:
        raise BankTransferConflict(
            "이미 처리 중인 카드 청구가 있어 자동 전환할 수 없습니다. "
            "운영자에게 문의해 주세요."
        )

    scheduled = list(
        Invoice.objects.select_for_update().filter(
            tenant_id=tenant_id,
            billing_mode="AUTO_CARD",
            status="SCHEDULED",
        )
    )
    for invoice in scheduled:
        invoice.billing_mode = "INVOICE_REQUEST"
        invoice.status = "PENDING"
        invoice.due_date = invoice.period_start + timedelta(days=15)
        invoice.save(
            update_fields=[
                "billing_mode",
                "status",
                "due_date",
                "updated_at",
            ]
        )

    if program.billing_mode != "INVOICE_REQUEST":
        program.billing_mode = "INVOICE_REQUEST"
        program.save(update_fields=["billing_mode", "updated_at"])

    invoice = (
        Invoice.objects.filter(
            tenant_id=tenant_id,
            billing_mode="INVOICE_REQUEST",
            status__in=("PENDING", "OVERDUE"),
            period_end__gte=timezone.localdate(),
        )
        .order_by("period_start")
        .first()
    )
    if invoice is None:
        invoice = invoice_service.create_for_next_period(program)
    if invoice is None:
        invoice = (
            Invoice.objects.filter(
                tenant_id=tenant_id,
                billing_mode="INVOICE_REQUEST",
                status__in=("PENDING", "OVERDUE"),
            )
            .order_by("-created_at")
            .first()
        )
    if invoice is None:
        raise BankTransferConflict(
            "현재 구독 상태에서는 새 청구서를 만들 수 없습니다. "
            "운영자에게 문의해 주세요."
        )
    return invoice


@transaction.atomic
def submit_notice(
    *,
    tenant_id: int,
    invoice_id: int,
    depositor_name: str,
    deposited_at,
    tax_invoice_requested: bool,
) -> BankTransferNotice:
    """고객 입금 신고를 생성하거나 반려 건을 재제출한다."""
    _require_available()
    invoice = (
        Invoice.objects.select_for_update()
        .filter(pk=invoice_id, tenant_id=tenant_id)
        .first()
    )
    if invoice is None:
        raise Invoice.DoesNotExist
    if invoice.billing_mode != "INVOICE_REQUEST":
        raise BankTransferConflict("계좌이체 청구서가 아닙니다.")
    if invoice.status not in ("PENDING", "OVERDUE"):
        raise BankTransferConflict(
            f"현재 청구 상태에서는 입금 신고를 할 수 없습니다: {invoice.status}"
        )
    if deposited_at > timezone.now() + timedelta(minutes=10):
        raise BankTransferConflict("이체 시각은 미래로 입력할 수 없습니다.")
    if deposited_at < invoice.created_at - timedelta(minutes=10):
        raise BankTransferConflict(
            "청구서가 생성되기 전의 이체 시각은 입력할 수 없습니다."
        )
    if Invoice.objects.filter(
        tenant_id=tenant_id,
        billing_mode="INVOICE_REQUEST",
        status__in=("PENDING", "FAILED", "OVERDUE"),
        period_start__lt=invoice.period_start,
    ).exists():
        raise BankTransferConflict(
            "먼저 납부해야 할 이전 청구서가 있습니다."
        )

    snapshot: dict = {}
    if tax_invoice_requested:
        profile = BusinessProfile.objects.filter(tenant_id=tenant_id).first()
        if profile is None:
            raise BankTransferConflict(
                "세금계산서 발행을 위해 사업자 정보를 먼저 저장해 주세요."
            )
        snapshot = profile.to_snapshot()

    notice = (
        BankTransferNotice.objects.select_for_update()
        .filter(invoice=invoice)
        .first()
    )
    now = timezone.now()
    if notice and notice.status == "SUBMITTED":
        if (
            notice.depositor_name == depositor_name
            and notice.deposited_at == deposited_at
            and notice.tax_invoice_requested == tax_invoice_requested
        ):
            return notice
        raise BankTransferConflict(
            "이미 입금 확인을 요청했습니다. 처리 결과를 기다려 주세요."
        )
    if notice and notice.status != "REJECTED":
        raise BankTransferConflict(
            "이미 입금 확인을 요청했습니다. 처리 결과를 기다려 주세요."
        )
    if notice is None:
        notice = BankTransferNotice(invoice=invoice)

    notice.depositor_name = depositor_name
    notice.deposited_at = deposited_at
    notice.amount = invoice.total_amount
    notice.status = "SUBMITTED"
    notice.tax_invoice_requested = tax_invoice_requested
    notice.business_profile_snapshot = snapshot
    notice.submitted_at = now
    notice.reviewed_at = None
    notice.reviewed_by = None
    notice.rejection_reason = ""
    notice.save()

    tax_issue = TaxInvoiceIssue.objects.filter(invoice=invoice).first()
    if tax_invoice_requested:
        if tax_issue and tax_issue.status == "ISSUED":
            raise BankTransferConflict("이미 세금계산서가 발행된 청구서입니다.")
        TaxInvoiceIssue.objects.update_or_create(
            invoice=invoice,
            defaults={
                "tenant_id": tenant_id,
                "business_profile_snapshot": snapshot,
                "status": "REQUESTED",
                "requested_at": now,
                "failure_reason": "",
            },
        )
    elif tax_issue and tax_issue.status != "ISSUED":
        tax_issue.status = "NOT_REQUESTED"
        tax_issue.business_profile_snapshot = {}
        tax_issue.requested_at = None
        tax_issue.failure_reason = ""
        tax_issue.save(
            update_fields=[
                "status",
                "business_profile_snapshot",
                "requested_at",
                "failure_reason",
                "updated_at",
            ]
        )
    return notice


@transaction.atomic
def confirm_notice(
    notice_id: int,
    *,
    reviewer,
) -> BankTransferNotice:
    """운영자가 입금을 확인하고 수납/구독/세금계산서 상태를 반영한다."""
    _program, invoice, notice = _lock_notice_workflow(notice_id)
    if notice.status == "CONFIRMED":
        return notice
    if notice.status != "SUBMITTED":
        raise BankTransferConflict(
            f"확인할 수 없는 입금 신고 상태입니다: {notice.status}"
        )
    if notice.amount != invoice.total_amount:
        raise BankTransferConflict(
            "입금 신고 금액과 현재 청구 금액이 다릅니다. "
            "금액을 확인한 뒤 신고를 다시 접수해 주세요."
        )

    invoice_service.confirm_manual_payment(
        notice.invoice_id,
        paid_at=notice.deposited_at,
    )
    notice.status = "CONFIRMED"
    notice.reviewed_at = timezone.now()
    notice.reviewed_by = reviewer
    notice.rejection_reason = ""
    notice.save(
        update_fields=[
            "status",
            "reviewed_at",
            "reviewed_by",
            "rejection_reason",
            "updated_at",
        ]
    )

    if notice.tax_invoice_requested:
        issue = TaxInvoiceIssue.objects.select_for_update().get(
            invoice_id=notice.invoice_id
        )
        if issue.status != "ISSUED":
            issue.status = "READY"
            issue.failure_reason = ""
            issue.save(
                update_fields=["status", "failure_reason", "updated_at"]
            )
    return notice


@transaction.atomic
def reject_notice(
    notice_id: int,
    *,
    reviewer,
    reason: str,
) -> BankTransferNotice:
    _program, _invoice, notice = _lock_notice_workflow(notice_id)
    if notice.status != "SUBMITTED":
        raise BankTransferConflict(
            f"반려할 수 없는 입금 신고 상태입니다: {notice.status}"
        )
    notice.status = "REJECTED"
    notice.reviewed_at = timezone.now()
    notice.reviewed_by = reviewer
    notice.rejection_reason = reason
    notice.save(
        update_fields=[
            "status",
            "reviewed_at",
            "reviewed_by",
            "rejection_reason",
            "updated_at",
        ]
    )

    issue = TaxInvoiceIssue.objects.select_for_update().filter(
        invoice_id=notice.invoice_id
    ).first()
    if issue and issue.status != "ISSUED":
        issue.status = "NOT_REQUESTED"
        issue.failure_reason = reason
        issue.save(
            update_fields=["status", "failure_reason", "updated_at"]
        )
    return notice


@transaction.atomic
def mark_tax_invoice_issued(
    issue_id: int,
    *,
    issue_number: str,
    issued_at=None,
) -> TaxInvoiceIssue:
    issue = TaxInvoiceIssue.objects.select_for_update().get(pk=issue_id)
    if issue.status == "ISSUED":
        if issue.issue_number == issue_number:
            return issue
        raise BankTransferConflict("이미 다른 승인번호로 발행 완료된 건입니다.")
    if issue.status != "READY":
        raise BankTransferConflict(
            f"발행 완료 처리할 수 없는 상태입니다: {issue.status}"
        )
    if TaxInvoiceIssue.objects.exclude(pk=issue.pk).filter(
        issue_number=issue_number
    ).exists():
        raise BankTransferConflict("이미 다른 발행 건에 사용된 승인번호입니다.")
    resolved_issued_at = issued_at or timezone.now()
    if (
        issue.requested_at
        and resolved_issued_at < issue.requested_at - timedelta(minutes=10)
    ):
        raise BankTransferConflict(
            "발행 요청 전의 시각으로 완료 처리할 수 없습니다."
        )
    if resolved_issued_at > timezone.now() + timedelta(minutes=10):
        raise BankTransferConflict("미래 시각으로 발행 완료 처리할 수 없습니다.")
    issue.status = "ISSUED"
    issue.issue_number = issue_number
    issue.issued_at = resolved_issued_at
    issue.failure_reason = ""
    issue.save(
        update_fields=[
            "status",
            "issue_number",
            "issued_at",
            "failure_reason",
            "updated_at",
        ]
    )
    return issue
