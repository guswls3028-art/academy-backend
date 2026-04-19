"""
PaymentService — 자동결제 실행의 유일한 진입점.

Phase D: PG 결제 실행 로직.

실행 흐름:
  1. Invoice 조회 (PENDING/SCHEDULED/FAILED만 허용)
  2. 활성 BillingKey 조회 (없으면 결제 실패 처리)
  3. PaymentTransaction 생성 (idempotency_key = provider_order_id)
  4. Toss API 호출 (트랜잭션 밖, HTTP 최대 60초)
  5. 결과 처리:
     - 성공: tx.status=SUCCESS + invoice.mark_paid() + 구독 갱신
     - 실패: tx.status=FAILED + invoice.mark_failed() + 재시도 예약

멱등성:
  - idempotency_key = invoice.provider_order_id (동일 인보이스 중복 결제 방지)
  - Toss orderId도 provider_order_id 사용 → 동일 주문 재호출 시 Toss가 중복 응답
  - PENDING tx가 이미 있으면 재시도 전에 조회해서 상태 동기화
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.billing.adapters.toss_payments import TossPaymentsClient
from apps.billing.models import BillingKey, Invoice, PaymentTransaction
from apps.billing.services import invoice_service

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class PaymentExecutionError(Exception):
    """결제 실행 단계에서 복구 불가능한 오류"""


def _get_client() -> TossPaymentsClient:
    return TossPaymentsClient()


def _get_active_billing_key(tenant_id: int) -> BillingKey | None:
    return (
        BillingKey.objects.filter(tenant_id=tenant_id, is_active=True)
        .select_related("billing_profile")
        .order_by("-created_at")
        .first()
    )


def _order_name_for(invoice: Invoice) -> str:
    """Toss orderName — 사용자 결제 화면/이메일에 노출"""
    return f"HakwonPlus {invoice.plan.upper()} ({invoice.period_start}~{invoice.period_end})"


@transaction.atomic
def _create_pending_tx(invoice: Invoice, billing_key: BillingKey) -> PaymentTransaction:
    """
    PENDING 상태 트랜잭션 생성. idempotency_key = provider_order_id.

    이미 동일 idempotency_key로 PENDING/SUCCESS tx가 있으면 그대로 재사용.
    """
    existing = PaymentTransaction.objects.filter(
        idempotency_key=invoice.provider_order_id,
    ).first()
    if existing:
        return existing

    return PaymentTransaction.objects.create(
        tenant_id=invoice.tenant_id,
        invoice=invoice,
        provider="tosspayments",
        payment_method="card",
        provider_order_id=invoice.provider_order_id,
        idempotency_key=invoice.provider_order_id,
        amount=invoice.total_amount,
        status="PENDING",
        card_company=billing_key.card_company,
        card_number_masked=billing_key.card_number_masked,
        request_payload={
            "billing_key_id": billing_key.id,
            "amount": invoice.total_amount,
            "order_id": invoice.provider_order_id,
        },
    )


@transaction.atomic
def _mark_tx_success(tx_id: int, *, toss_payload: dict) -> PaymentTransaction:
    tx = PaymentTransaction.objects.select_for_update().get(pk=tx_id)
    tx.status = "SUCCESS"
    tx.provider_payment_key = toss_payload.get("paymentKey", "")
    tx.transaction_key = toss_payload.get("paymentKey", "")
    tx.response_payload = toss_payload
    tx.raw_response = toss_payload
    tx.processed_at = timezone.now()
    card = toss_payload.get("card") or {}
    if card:
        tx.card_company = card.get("company", tx.card_company) or tx.card_company
        tx.card_number_masked = card.get("number", tx.card_number_masked) or tx.card_number_masked
    tx.save(update_fields=[
        "status", "provider_payment_key", "transaction_key",
        "response_payload", "raw_response", "processed_at",
        "card_company", "card_number_masked", "updated_at",
    ])
    return tx


@transaction.atomic
def _mark_tx_failed(tx_id: int, *, reason: str, toss_payload: dict) -> PaymentTransaction:
    tx = PaymentTransaction.objects.select_for_update().get(pk=tx_id)
    tx.status = "FAILED"
    tx.failure_reason = reason[:500]
    tx.response_payload = toss_payload
    tx.raw_response = toss_payload
    tx.processed_at = timezone.now()
    tx.save(update_fields=[
        "status", "failure_reason", "response_payload", "raw_response",
        "processed_at", "updated_at",
    ])
    return tx


def execute_auto_payment(invoice_id: int) -> dict:
    """
    자동결제 실행. 멱등성 보장.

    반환:
      {
        "success": bool,
        "invoice_id": int,
        "tx_id": int | None,
        "reason": str,  # 실패 시
        "payment_key": str,  # 성공 시
      }

    사전 조건:
      - invoice.billing_mode == "AUTO_CARD"
      - invoice.status in (SCHEDULED, PENDING, FAILED)
      - tenant가 BILLING_EXEMPT 아님
      - 활성 BillingKey 존재
    """
    invoice = Invoice.objects.select_related("tenant").get(pk=invoice_id)

    # ──── 사전 체크 ────
    if invoice.tenant_id in settings.BILLING_EXEMPT_TENANT_IDS:
        logger.info("Skip payment for exempt tenant=%s invoice=%s", invoice.tenant_id, invoice.invoice_number)
        return {"success": False, "invoice_id": invoice_id, "tx_id": None, "reason": "exempt_tenant"}

    if invoice.billing_mode != "AUTO_CARD":
        return {"success": False, "invoice_id": invoice_id, "tx_id": None,
                "reason": f"billing_mode={invoice.billing_mode}, not AUTO_CARD"}

    # 이미 결제 완료된 인보이스는 멱등적으로 success 반환 (중복 호출 안전)
    if invoice.status == "PAID":
        tx = PaymentTransaction.objects.filter(
            idempotency_key=invoice.provider_order_id,
            status="SUCCESS",
        ).first()
        return {
            "success": True,
            "invoice_id": invoice_id,
            "tx_id": tx.id if tx else None,
            "reason": "already_paid",
            "payment_key": tx.provider_payment_key if tx else "",
        }

    if invoice.status not in ("SCHEDULED", "PENDING", "FAILED"):
        return {"success": False, "invoice_id": invoice_id, "tx_id": None,
                "reason": f"invoice status={invoice.status}, not chargeable"}

    if not settings.TOSS_AUTO_BILLING_ENABLED:
        logger.warning(
            "Payment skipped: TOSS_AUTO_BILLING_ENABLED=False invoice=%s",
            invoice.invoice_number,
        )
        return {"success": False, "invoice_id": invoice_id, "tx_id": None,
                "reason": "TOSS_AUTO_BILLING_ENABLED is False"}

    # ──── 빌링키 조회 ────
    billing_key = _get_active_billing_key(invoice.tenant_id)
    if not billing_key:
        logger.warning("No active billing key for tenant=%s invoice=%s",
                       invoice.tenant_id, invoice.invoice_number)
        # 결제 시도 없이 바로 FAILED 처리 (재시도 예약 포함)
        if invoice.status != "FAILED":
            if invoice.status == "SCHEDULED":
                invoice_service.transition_to_pending(invoice.pk)
            invoice_service.mark_failed(invoice.pk, reason="NO_ACTIVE_BILLING_KEY")
        return {"success": False, "invoice_id": invoice_id, "tx_id": None,
                "reason": "no_active_billing_key"}

    # ──── 상태 전이: SCHEDULED → PENDING ────
    if invoice.status == "SCHEDULED":
        invoice = invoice_service.transition_to_pending(invoice.pk)
    elif invoice.status == "FAILED":
        # 재시도 전 PENDING 복귀
        invoice = invoice_service.retry_pending(invoice.pk)

    # ──── PaymentTransaction 생성 (멱등) ────
    tx = _create_pending_tx(invoice, billing_key)

    # 이미 SUCCESS면 invoice만 동기화하고 종료
    if tx.status == "SUCCESS":
        if invoice.status != "PAID":
            invoice_service.mark_paid(invoice.pk, paid_at=tx.processed_at)
        return {"success": True, "invoice_id": invoice_id, "tx_id": tx.id,
                "reason": "already_paid", "payment_key": tx.provider_payment_key}

    # ──── Toss API 호출 (트랜잭션 밖) ────
    client = _get_client()
    profile = billing_key.billing_profile
    result = client.charge_with_billing_key(
        billing_key=billing_key.billing_key,
        customer_key=profile.provider_customer_key,
        amount=invoice.total_amount,
        order_id=invoice.provider_order_id,
        order_name=_order_name_for(invoice),
        customer_email=profile.payer_email,
        customer_name=profile.payer_name,
    )

    # ──── 결과 처리 ────
    if result.get("success"):
        _mark_tx_success(tx.id, toss_payload=result)
        invoice_service.mark_paid(invoice.pk)
        logger.info(
            "Auto payment SUCCESS: tenant=%s invoice=%s amount=%s paymentKey=%s",
            invoice.tenant_id, invoice.invoice_number, invoice.total_amount,
            result.get("paymentKey", ""),
        )
        return {"success": True, "invoice_id": invoice_id, "tx_id": tx.id,
                "payment_key": result.get("paymentKey", ""), "reason": "ok"}

    error_code = result.get("error_code", "UNKNOWN")
    error_msg = result.get("error_message", "Unknown error")
    reason = f"[{error_code}] {error_msg}"
    _mark_tx_failed(tx.id, reason=reason, toss_payload=result)
    invoice_service.mark_failed(invoice.pk, reason=reason)

    logger.warning(
        "Auto payment FAILED: tenant=%s invoice=%s reason=%s",
        invoice.tenant_id, invoice.invoice_number, reason,
    )
    return {"success": False, "invoice_id": invoice_id, "tx_id": tx.id, "reason": reason}
