"""
Toss 웹훅 이벤트 처리.

Toss는 비동기로 결제 상태 변경을 웹훅으로 통지한다.
동기 응답으로 놓친 상태(네트워크 끊김, 타임아웃 후 성공 등)를 재동기화.

주요 이벤트:
  - PAYMENT.STATUS_CHANGED / PAYMENT_STATUS_CHANGED
    data: { orderId, paymentKey, status, ... }
    status: READY, IN_PROGRESS, DONE, CANCELED, PARTIAL_CANCELED, ABORTED, EXPIRED

로컬 상태 매핑:
  DONE                    → PaymentTransaction=SUCCESS + Invoice=PAID + 구독 갱신
  ABORTED / EXPIRED       → PaymentTransaction=FAILED + Invoice=FAILED
  CANCELED                → PaymentTransaction=REFUNDED (환불)
  PARTIAL_CANCELED        → refunded_amount 업데이트
  READY / IN_PROGRESS     → 상태 유지 (아무 것도 안 함)

멱등성:
  - orderId 기준 PaymentTransaction 조회
  - 이미 종단 상태면 스킵
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.models import Invoice, PaymentTransaction
from apps.billing.services import invoice_service

logger = logging.getLogger(__name__)


def _parse_datetime(value: str | None):
    if not value:
        return None
    # Toss: "2026-04-20T12:34:56+09:00"
    try:
        from django.utils.dateparse import parse_datetime
        return parse_datetime(value)
    except Exception:
        return None


@transaction.atomic
def _handle_done(tx: PaymentTransaction, data: dict[str, Any]) -> str:
    # 외부 atomic의 SELECT FOR UPDATE는 이미 종료됨. 동시 webhook race 차단을 위해
    # 핸들러 내부에서 다시 row lock + 최신 상태 확인.
    tx = PaymentTransaction.objects.select_for_update().select_related("invoice").get(pk=tx.pk)

    if tx.status == "SUCCESS":
        return "already_success"
    # 상태 역전 방어: 환불(REFUNDED/PARTIALLY_REFUNDED) 후 지연 도착한 DONE이
    # SUCCESS로 덮어쓰면 환불 사실이 사라진다. 종단 상태는 보존.
    if tx.status in ("REFUNDED", "PARTIALLY_REFUNDED"):
        logger.warning(
            "Webhook DONE ignored — terminal refund state: tx=%s status=%s",
            tx.id, tx.status,
        )
        return f"terminal_{tx.status.lower()}"

    tx.status = "SUCCESS"
    tx.provider_payment_key = data.get("paymentKey", tx.provider_payment_key)
    tx.transaction_key = data.get("paymentKey", tx.transaction_key)
    tx.response_payload = data
    tx.raw_response = data
    tx.processed_at = _parse_datetime(data.get("approvedAt")) or timezone.now()
    card = data.get("card") or {}
    if card:
        tx.card_company = card.get("company", tx.card_company) or tx.card_company
        tx.card_number_masked = card.get("number", tx.card_number_masked) or tx.card_number_masked
    tx.save(update_fields=[
        "status", "provider_payment_key", "transaction_key",
        "response_payload", "raw_response", "processed_at",
        "card_company", "card_number_masked", "updated_at",
    ])

    if tx.invoice_id and tx.invoice.status != "PAID":
        # FAILED 상태면 PENDING 복귀 후 PAID
        if tx.invoice.status == "FAILED":
            invoice_service.retry_pending(tx.invoice_id)
        elif tx.invoice.status == "SCHEDULED":
            invoice_service.transition_to_pending(tx.invoice_id)
        invoice_service.mark_paid(tx.invoice_id, paid_at=tx.processed_at)

    logger.info(
        "Webhook DONE applied: tx=%s invoice=%s paymentKey=%s",
        tx.id, tx.invoice_id, tx.provider_payment_key,
    )
    return "applied_done"


@transaction.atomic
def _handle_failed(tx: PaymentTransaction, data: dict[str, Any], reason: str) -> str:
    tx = PaymentTransaction.objects.select_for_update().select_related("invoice").get(pk=tx.pk)
    if tx.status in ("SUCCESS", "REFUNDED", "PARTIALLY_REFUNDED"):
        # 종단 상태 덮어쓰지 않음 — SUCCESS/환불 후 지연된 ABORTED/EXPIRED 무시.
        return f"terminal_{tx.status.lower()}"
    if tx.status == "FAILED":
        # 멱등: 동일 실패 이벤트 재수신 시 noop.
        return "already_failed"

    tx.status = "FAILED"
    tx.failure_reason = reason[:500]
    tx.response_payload = data
    tx.raw_response = data
    tx.processed_at = timezone.now()
    tx.save(update_fields=[
        "status", "failure_reason", "response_payload", "raw_response",
        "processed_at", "updated_at",
    ])

    if tx.invoice_id and tx.invoice.status == "PENDING":
        invoice_service.mark_failed(tx.invoice_id, reason=reason)

    logger.warning(
        "Webhook FAILED applied: tx=%s invoice=%s reason=%s",
        tx.id, tx.invoice_id, reason,
    )
    return "applied_failed"


@transaction.atomic
def _handle_canceled(tx: PaymentTransaction, data: dict[str, Any]) -> str:
    tx = PaymentTransaction.objects.select_for_update().select_related("invoice").get(pk=tx.pk)
    if tx.status == "REFUNDED":
        return "already_refunded"
    # 환불은 결제 성공 또는 부분 환불 상태에서만 가능. PENDING/FAILED 결제는
    # 환불할 게 없으므로 운영 알람용 로그만 남기고 상태 변경 안 함.
    if tx.status not in ("SUCCESS", "PARTIALLY_REFUNDED"):
        logger.warning(
            "Webhook CANCELED ignored — non-refundable status: tx=%s status=%s",
            tx.id, tx.status,
        )
        return f"non_refundable_{tx.status.lower()}"

    tx.status = "REFUNDED"
    tx.refunded_amount = tx.amount
    tx.refunded_at = timezone.now()
    tx.response_payload = data
    tx.raw_response = data
    tx.save(update_fields=[
        "status", "refunded_amount", "refunded_at",
        "response_payload", "raw_response", "updated_at",
    ])

    logger.info("Webhook CANCELED applied: tx=%s invoice=%s", tx.id, tx.invoice_id)
    return "applied_canceled"


@transaction.atomic
def _handle_partial_canceled(tx: PaymentTransaction, data: dict[str, Any]) -> str:
    tx = PaymentTransaction.objects.select_for_update().select_related("invoice").get(pk=tx.pk)
    total_canceled = data.get("totalAmount", 0) - data.get("balanceAmount", tx.amount)
    if total_canceled <= 0:
        return "no_cancel_amount"
    # 부분 환불도 결제가 성공·부분환불 상태일 때만 의미 있음.
    if tx.status not in ("SUCCESS", "PARTIALLY_REFUNDED"):
        logger.warning(
            "Webhook PARTIAL_CANCELED ignored — non-refundable status: tx=%s status=%s",
            tx.id, tx.status,
        )
        return f"non_refundable_{tx.status.lower()}"
    tx.status = "PARTIALLY_REFUNDED"
    tx.refunded_amount = int(total_canceled)
    tx.refunded_at = timezone.now()
    tx.response_payload = data
    tx.raw_response = data
    tx.save(update_fields=[
        "status", "refunded_amount", "refunded_at",
        "response_payload", "raw_response", "updated_at",
    ])
    return "applied_partial_refund"


def handle_payment_status(data: dict[str, Any]) -> dict[str, Any]:
    """
    PAYMENT_STATUS_CHANGED 이벤트 처리.

    data 예시 (Toss):
      {
        "paymentKey": "...",
        "orderId": "ord_...",
        "status": "DONE",
        "approvedAt": "2026-04-20T12:34:56+09:00",
        "card": { "company": "삼성", "number": "****-****-****-1234" },
        "totalAmount": 217800,
        ...
      }

    반환: {"result": "...", "orderId": "...", "status": "..."}
    """
    order_id = data.get("orderId")
    status = data.get("status")

    if not order_id or not status:
        logger.warning("Webhook ignored: missing orderId/status. data=%s", data)
        return {"result": "invalid_payload", "orderId": order_id, "status": status}

    with transaction.atomic():
        tx = (
            PaymentTransaction.objects
            .select_for_update()
            .select_related("invoice")
            .filter(provider_order_id=order_id)
            .order_by("-created_at")
            .first()
        )

        if not tx:
            # orderId로 찾지 못하면 invoice 조회 시도 (invoice가 생겼지만 tx가 없는 상태)
            try:
                invoice = Invoice.objects.select_for_update().get(provider_order_id=order_id)
            except Invoice.DoesNotExist:
                logger.warning("Webhook unmatched: orderId=%s", order_id)
                return {"result": "unmatched", "orderId": order_id, "status": status}

            try:
                # 최소한의 tx 기록 생성 (비자동결제 경로로 수동 결제된 경우 등)
                tx = PaymentTransaction.objects.create(
                    tenant_id=invoice.tenant_id,
                    invoice=invoice,
                    provider="tosspayments",
                    provider_order_id=order_id,
                    idempotency_key=order_id,
                    amount=invoice.total_amount,
                    status="PENDING",
                    payment_method="card",
                    request_payload={"source": "webhook_recovery"},
                )
            except IntegrityError:
                # 동시 웹훅으로 동일 idempotency_key가 먼저 생성된 경우 재조회
                tx = (
                    PaymentTransaction.objects
                    .select_for_update()
                    .select_related("invoice")
                    .filter(idempotency_key=order_id)
                    .order_by("-created_at")
                    .first()
                )
                if not tx:
                    logger.exception("Webhook tx recovery failed after IntegrityError: orderId=%s", order_id)
                    return {"result": "conflict_retry", "orderId": order_id, "status": status}

    if status == "DONE":
        result = _handle_done(tx, data)
    elif status in ("ABORTED", "EXPIRED"):
        result = _handle_failed(tx, data, reason=f"Toss status={status}")
    elif status == "CANCELED":
        result = _handle_canceled(tx, data)
    elif status == "PARTIAL_CANCELED":
        result = _handle_partial_canceled(tx, data)
    else:
        # READY, IN_PROGRESS 등 — 상태 유지
        result = f"noop_{status.lower()}"

    return {"result": result, "orderId": order_id, "status": status, "tx_id": tx.id}
