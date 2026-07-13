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
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.adapters.toss_payments import (
    TOSS_AMBIGUOUS_MUTATION_ERROR_CODES,
    TossPaymentsClient,
)
from apps.billing.models import BillingKey, Invoice, PaymentTransaction
from apps.billing.services import invoice_service
from apps.billing.services.billing_key_crypto import (
    BillingKeyCryptoError,
    decrypt_billing_key,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class PaymentExecutionError(Exception):
    """결제 실행 단계에서 복구 불가능한 오류"""


def _get_client() -> TossPaymentsClient:
    return TossPaymentsClient()


def _record_payment_security_failure(
    *,
    tenant_id: int,
    invoice_id: int,
    billing_key_id: int | None,
    stage: str,
    error: BaseException,
) -> None:
    """Persist metadata-only evidence for local credential/config failures."""
    try:
        from apps.core.models import OpsAuditLog

        OpsAuditLog.objects.create(
            action="billing.payment_configuration_failed",
            summary="Automatic payment blocked before provider call",
            target_tenant_id=tenant_id,
            payload={
                "invoice_id": invoice_id,
                "billing_key_id": billing_key_id,
                "stage": stage,
            },
            result=OpsAuditLog.Result.FAILED,
            error=type(error).__name__,
        )
    except Exception:
        logger.exception(
            "Could not persist payment configuration evidence: tenant=%s invoice=%s stage=%s",
            tenant_id,
            invoice_id,
            stage,
        )


def _order_name_for(invoice: Invoice) -> str:
    """Toss orderName — 사용자 결제 화면/이메일에 노출"""
    return f"HakwonPlus {invoice.plan.upper()} ({invoice.period_start}~{invoice.period_end})"


@transaction.atomic
def _create_pending_tx(invoice: Invoice, billing_key: BillingKey) -> PaymentTransaction:
    """
    PENDING 상태 트랜잭션 생성. idempotency_key = provider_order_id.

    이미 동일 idempotency_key로 PENDING/SUCCESS tx가 있으면 그대로 재사용.
    동시성 race 시 unique 제약(idempotency_key)이 IntegrityError 를 발생시키므로,
    예외를 잡고 기존 row 를 다시 조회해서 멱등 동작을 유지한다.
    """
    existing = PaymentTransaction.objects.filter(
        idempotency_key=invoice.provider_order_id,
    ).order_by("id").first()
    if existing:
        return existing

    try:
        # Isolate the unique-race failure in its own savepoint.  Catching an
        # IntegrityError in this function's outer atomic block would leave the
        # transaction broken and make the reconciliation query fail with
        # TransactionManagementError.
        with transaction.atomic():
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
    except IntegrityError:
        existing = PaymentTransaction.objects.filter(
            idempotency_key=invoice.provider_order_id,
        ).order_by("id").first()
        if existing:
            return existing
        raise


@transaction.atomic
def _claim_payment_attempt(
    invoice_id: int,
) -> dict:
    """Claim the sole provider-call right for one invoice.

    Program is locked before Invoice so cancellation and payment use the same
    lock order.  PROCESSING is intentionally not reclaimed automatically: a
    crash after this commit cannot prove whether the PG accepted the request.
    """
    from apps.core.models import Program

    tenant_id = Invoice.objects.only("tenant_id").get(pk=invoice_id).tenant_id
    program = Program.objects.select_for_update().get(tenant_id=tenant_id)
    invoice = Invoice.objects.select_for_update().select_related("tenant").get(pk=invoice_id)

    if (
        program.cancel_at_period_end
        and program.subscription_expires_at is not None
        and invoice.period_start > program.subscription_expires_at
    ):
        if invoice.status not in {"PAID", "VOID"}:
            invoice_service.void(invoice.pk, reason="cancel_at_period_end")
        return {"claimed": False, "reason": "cancel_at_period_end", "invoice": invoice}

    if invoice.status == "PAID":
        return {"claimed": False, "reason": "already_paid", "invoice": invoice}
    if invoice.status not in ("SCHEDULED", "PENDING", "FAILED"):
        return {
            "claimed": False,
            "reason": f"invoice status={invoice.status}, not chargeable",
            "invoice": invoice,
        }

    if invoice.status == "SCHEDULED":
        invoice = invoice_service.transition_to_pending(invoice.pk)
    elif invoice.status == "FAILED":
        invoice = invoice_service.retry_pending(invoice.pk)

    billing_key = (
        BillingKey.objects.select_for_update()
        .filter(tenant_id=tenant_id, is_active=True)
        .select_related("billing_profile")
        .order_by("-created_at")
        .first()
    )
    if billing_key is None:
        if invoice.status == "SCHEDULED":
            invoice = invoice_service.transition_to_pending(invoice.pk)
        if invoice.status != "FAILED":
            invoice = invoice_service.mark_failed(
                invoice.pk,
                reason="NO_ACTIVE_BILLING_KEY",
            )
        return {
            "claimed": False,
            "reason": "no_active_billing_key",
            "invoice": invoice,
        }

    try:
        provider_billing_key = decrypt_billing_key(billing_key.billing_key)
    except BillingKeyCryptoError as exc:
        return {
            "claimed": False,
            "reason": "billing_credential_unavailable",
            "invoice": invoice,
            "billing_key": billing_key,
            "configuration_error": exc,
        }

    tx = _create_pending_tx(invoice, billing_key)
    tx = PaymentTransaction.objects.select_for_update().get(pk=tx.pk)
    if tx.status == "SUCCESS":
        return {
            "claimed": False,
            "reason": "transaction_already_succeeded",
            "invoice": invoice,
            "tx": tx,
        }
    if tx.status == "PROCESSING":
        return {
            "claimed": False,
            "reason": "payment_in_progress",
            "invoice": invoice,
            "tx": tx,
        }
    if tx.status not in {"PENDING", "FAILED"}:
        return {
            "claimed": False,
            "reason": f"payment transaction status={tx.status}, not retryable",
            "invoice": invoice,
            "tx": tx,
        }

    tx.status = "PROCESSING"
    tx.processing_started_at = timezone.now()
    tx.failure_reason = ""
    tx.save(update_fields=[
        "status",
        "processing_started_at",
        "failure_reason",
        "updated_at",
    ])
    return {
        "claimed": True,
        "reason": "claimed",
        "invoice": invoice,
        "tx": tx,
        "billing_key": billing_key,
        "provider_billing_key": provider_billing_key,
    }


@transaction.atomic
def _mark_tx_success(tx_id: int, *, toss_payload: dict) -> PaymentTransaction:
    tx = PaymentTransaction.objects.select_for_update().get(pk=tx_id)
    if tx.status == "SUCCESS":
        return tx
    if tx.status != "PROCESSING":
        raise PaymentExecutionError(
            f"Cannot mark payment success from status={tx.status} tx={tx.id}"
        )
    tx.status = "SUCCESS"
    tx.provider_payment_key = toss_payload.get("paymentKey", "")
    tx.transaction_key = toss_payload.get("paymentKey", "")
    tx.response_payload = toss_payload
    tx.raw_response = toss_payload
    tx.processed_at = timezone.now()
    tx.processing_started_at = None
    card = toss_payload.get("card") or {}
    if card:
        tx.card_company = card.get("company", tx.card_company) or tx.card_company
        tx.card_number_masked = card.get("number", tx.card_number_masked) or tx.card_number_masked
    tx.save(update_fields=[
        "status", "provider_payment_key", "transaction_key",
        "response_payload", "raw_response", "processed_at",
        "card_company", "card_number_masked", "processing_started_at", "updated_at",
    ])
    return tx


@transaction.atomic
def _mark_tx_failed(tx_id: int, *, reason: str, toss_payload: dict) -> PaymentTransaction:
    tx = PaymentTransaction.objects.select_for_update().get(pk=tx_id)
    if tx.status == "FAILED":
        return tx
    if tx.status != "PROCESSING":
        raise PaymentExecutionError(
            f"Cannot mark payment failure from status={tx.status} tx={tx.id}"
        )
    tx.status = "FAILED"
    tx.failure_reason = reason[:500]
    tx.response_payload = toss_payload
    tx.raw_response = toss_payload
    tx.processed_at = timezone.now()
    tx.processing_started_at = None
    tx.save(update_fields=[
        "status", "failure_reason", "response_payload", "raw_response",
        "processed_at", "processing_started_at", "updated_at",
    ])
    return tx


@transaction.atomic
def _release_tx_after_local_failure(tx_id: int, *, reason: str) -> PaymentTransaction:
    """Return a pre-provider claim to PENDING without charging customer retries."""
    tx = PaymentTransaction.objects.select_for_update().get(pk=tx_id)
    if tx.status != "PROCESSING":
        return tx
    tx.status = "PENDING"
    tx.failure_reason = reason[:500]
    tx.processing_started_at = None
    tx.save(
        update_fields=[
            "status",
            "failure_reason",
            "processing_started_at",
            "updated_at",
        ]
    )
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

    claim = _claim_payment_attempt(invoice.pk)
    invoice = claim["invoice"]
    tx = claim.get("tx")
    if claim["reason"] in {"already_paid", "transaction_already_succeeded"}:
        if tx is None:
            tx = PaymentTransaction.objects.filter(
                idempotency_key=invoice.provider_order_id,
                status="SUCCESS",
            ).first()
        if invoice.status != "PAID":
            invoice_service.mark_paid(
                invoice.pk,
                paid_at=tx.processed_at if tx else None,
            )
        return {
            "success": True,
            "invoice_id": invoice_id,
            "tx_id": tx.id if tx else None,
            "reason": "already_paid",
            "payment_key": tx.provider_payment_key if tx else "",
        }
    if not claim["claimed"]:
        if claim["reason"] == "no_active_billing_key":
            logger.warning(
                "No active billing key for tenant=%s invoice=%s",
                invoice.tenant_id,
                invoice.invoice_number,
            )
        if claim["reason"] == "billing_credential_unavailable":
            error = claim["configuration_error"]
            billing_key = claim["billing_key"]
            _record_payment_security_failure(
                tenant_id=invoice.tenant_id,
                invoice_id=invoice.pk,
                billing_key_id=billing_key.id,
                stage="decrypt",
                error=error,
            )
            logger.critical(
                "Automatic payment blocked by local billing credential failure: tenant=%s invoice=%s key_id=%s error=%s",
                invoice.tenant_id,
                invoice.invoice_number,
                billing_key.id,
                type(error).__name__,
            )
        return {
            "success": False,
            "invoice_id": invoice_id,
            "tx_id": tx.id if tx else None,
            "reason": claim["reason"],
        }

    billing_key = claim["billing_key"]

    # ──── Toss API 호출 (트랜잭션 밖) ────
    try:
        client = _get_client()
        profile = billing_key.billing_profile
        charge_kwargs = {
            "billing_key": claim["provider_billing_key"],
            "customer_key": profile.provider_customer_key,
            "amount": invoice.total_amount,
            "order_id": invoice.provider_order_id,
            "order_name": _order_name_for(invoice),
            "customer_email": profile.payer_email,
            "customer_name": profile.payer_name,
        }
    except Exception as exc:
        _release_tx_after_local_failure(
            tx.id,
            reason="PAYMENT_CONFIGURATION_UNAVAILABLE",
        )
        _record_payment_security_failure(
            tenant_id=invoice.tenant_id,
            invoice_id=invoice.pk,
            billing_key_id=billing_key.id,
            stage="client_init",
            error=exc,
        )
        logger.exception(
            "Auto payment client setup failed before provider call: tenant=%s invoice=%s tx=%s",
            invoice.tenant_id,
            invoice.invoice_number,
            tx.id,
        )
        return {
            "success": False,
            "invoice_id": invoice_id,
            "tx_id": tx.id,
            "reason": "payment_configuration_unavailable",
        }

    try:
        result = client.charge_with_billing_key(**charge_kwargs)
    except Exception as exc:
        logger.critical(
            "Auto payment provider outcome unknown; reconciliation required: "
            "tenant=%s invoice=%s tx=%s error=%s",
            invoice.tenant_id,
            invoice.invoice_number,
            tx.id,
            exc,
            exc_info=True,
        )
        return {
            "success": False,
            "invoice_id": invoice_id,
            "tx_id": tx.id,
            "reason": "provider_outcome_unknown",
            "reconciliation_required": True,
        }

    # ──── 결과 처리 ────
    if result.get("success"):
        provider_response_matches = (
            result.get("type") == "BILLING"
            and result.get("orderId") == invoice.provider_order_id
            and type(result.get("totalAmount")) is int
            and result.get("totalAmount") == invoice.total_amount
            and result.get("status") == "DONE"
            and isinstance(result.get("paymentKey"), str)
            and bool(result.get("paymentKey").strip())
        )
        if not provider_response_matches:
            logger.critical(
                "Auto payment response mismatch; reconciliation required: "
                "tenant=%s invoice=%s tx=%s order_match=%s amount_match=%s "
                "type=%s status=%s",
                invoice.tenant_id,
                invoice.invoice_number,
                tx.id,
                result.get("orderId") == invoice.provider_order_id,
                result.get("totalAmount") == invoice.total_amount,
                result.get("type"),
                result.get("status"),
            )
            return {
                "success": False,
                "invoice_id": invoice_id,
                "tx_id": tx.id,
                "reason": "provider_response_mismatch",
                "reconciliation_required": True,
            }
        _mark_tx_success(tx.id, toss_payload=result)
        invoice_service.mark_paid(invoice.pk)
        logger.info(
            "Auto payment SUCCESS: tenant=%s invoice=%s amount=%s",
            invoice.tenant_id, invoice.invoice_number, invoice.total_amount,
        )
        return {"success": True, "invoice_id": invoice_id, "tx_id": tx.id,
                "payment_key": result.get("paymentKey", ""), "reason": "ok"}

    error_code = result.get("error_code", "UNKNOWN")
    error_msg = result.get("error_message", "Unknown error")
    reason = f"[{error_code}] {error_msg}"
    if (
        result.get("outcome_unknown")
        or error_code in TOSS_AMBIGUOUS_MUTATION_ERROR_CODES
    ):
        logger.critical(
            "Auto payment provider outcome unknown; reconciliation required: "
            "tenant=%s invoice=%s tx=%s code=%s",
            invoice.tenant_id,
            invoice.invoice_number,
            tx.id,
            error_code,
        )
        return {
            "success": False,
            "invoice_id": invoice_id,
            "tx_id": tx.id,
            "reason": "provider_outcome_unknown",
            "reconciliation_required": True,
        }
    _mark_tx_failed(tx.id, reason=reason, toss_payload=result)
    invoice_service.mark_failed(invoice.pk, reason=reason)

    logger.warning(
        "Auto payment FAILED: tenant=%s invoice=%s reason=%s",
        invoice.tenant_id, invoice.invoice_number, reason,
    )
    return {"success": False, "invoice_id": invoice_id, "tx_id": tx.id, "reason": reason}
