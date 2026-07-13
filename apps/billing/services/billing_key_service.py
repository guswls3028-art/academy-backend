"""
빌링키(카드 등록) 서비스 레이어

규칙:
- 테넌트당 활성 빌링키는 1개만 허용
- 새 빌링키 발급 시 기존 활성 키는 비활성화
- 발급/교체/삭제는 Program 잠금으로 결제 claim과 직렬화
- PROCESSING 결제가 있으면 공급사 호출 전에 카드 변경을 차단
- 삭제 시 Toss 성공 후에만 로컬 비활성화
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.billing.adapters.toss_payments import TossPaymentsClient
from apps.billing.models import BillingKey, BillingProfile
from apps.billing.services.billing_key_crypto import (
    BillingKeyCryptoError,
    decrypt_billing_key,
    encrypt_billing_key,
)

logger = logging.getLogger(__name__)


class BillingProviderOutcomeUnknown(ValueError):
    """Provider may have applied a card mutation; manual reconciliation required."""


class BillingCredentialUnavailable(ValueError):
    """Local billing credential/configuration failed before provider mutation."""


def _record_card_reconciliation_required(
    *,
    tenant_id: int,
    operation: str,
    billing_key_id: int | None = None,
    error: BaseException | None = None,
) -> None:
    """Persist metadata-only operator evidence outside the failed transaction."""
    try:
        from apps.core.models import OpsAuditLog

        OpsAuditLog.objects.create(
            action="billing.card_reconciliation_required",
            summary=f"Billing card {operation} requires provider reconciliation",
            target_tenant_id=tenant_id,
            payload={
                "operation": operation,
                "billing_key_id": billing_key_id,
            },
            result=OpsAuditLog.Result.FAILED,
            error=(type(error).__name__ if error is not None else "provider_outcome_unknown"),
        )
    except Exception:
        logger.exception(
            "Could not persist billing card reconciliation evidence: tenant=%s operation=%s key_id=%s",
            tenant_id,
            operation,
            billing_key_id,
        )


def _record_card_security_failure(
    *,
    tenant_id: int,
    operation: str,
    billing_key_id: int | None,
    stage: str,
    error: BaseException,
) -> None:
    """Persist metadata-only evidence without mislabeling provider state."""
    try:
        from apps.core.models import OpsAuditLog

        OpsAuditLog.objects.create(
            action="billing.card_configuration_failed",
            summary=f"Billing card {operation} blocked before provider call",
            target_tenant_id=tenant_id,
            payload={
                "operation": operation,
                "billing_key_id": billing_key_id,
                "stage": stage,
            },
            result=OpsAuditLog.Result.FAILED,
            error=type(error).__name__,
        )
    except Exception:
        logger.exception(
            "Could not persist billing card security evidence: tenant=%s operation=%s key_id=%s",
            tenant_id,
            operation,
            billing_key_id,
        )


def _validate_issued_billing_key(
    result: Mapping,
    *,
    expected_customer_key: str,
) -> tuple[str, str, str]:
    """Validate the provider success contract before any local state mutation."""
    billing_key = result.get("billingKey")
    customer_key = result.get("customerKey")
    card = result.get("card")
    if (
        not isinstance(billing_key, str)
        or not billing_key.strip()
        or len(billing_key) > 200
        or not isinstance(customer_key, str)
        or customer_key != expected_customer_key
        or not isinstance(card, Mapping)
    ):
        raise ValueError("invalid_provider_success_payload")

    company = card.get("company", "")
    number = card.get("number", "")
    if (
        not isinstance(company, str)
        or len(company) > 50
        or not isinstance(number, str)
        or len(number) > 20
    ):
        raise ValueError("invalid_provider_card_payload")
    return billing_key, company, number


def _lock_card_mutation_scope(tenant_id: int) -> None:
    """Serialize card mutation with payment claim using Program as tenant mutex."""
    from apps.billing.models import PaymentTransaction
    from apps.core.models.program import Program

    Program.objects.select_for_update().get(tenant_id=tenant_id)
    processing_ids = list(
        PaymentTransaction.objects.select_for_update()
        .filter(tenant_id=tenant_id, status="PROCESSING")
        .order_by("id")
        .values_list("id", flat=True)[:1]
    )
    if processing_ids:
        raise ValueError(
            "결제 결과를 확인 중이므로 카드를 변경할 수 없습니다. 결제 확인 후 다시 시도해 주세요."
        )


def _get_client() -> TossPaymentsClient:
    return TossPaymentsClient()


@transaction.atomic
def _get_or_create_billing_profile(tenant_id: int) -> BillingProfile:
    """Serialize one-to-one profile creation on the canonical Tenant mutex."""
    from apps.core.models import Tenant

    Tenant.objects.select_for_update().only("id").get(pk=tenant_id)
    profile, _created = BillingProfile.objects.get_or_create(
        tenant_id=tenant_id,
        defaults={"provider": "tosspayments"},
    )
    return profile


def get_or_create_customer_key(tenant_id: int) -> str:
    """
    BillingProfile의 provider_customer_key를 반환한다.
    BillingProfile이 없으면 생성한다.
    """
    profile = _get_or_create_billing_profile(tenant_id)
    return profile.provider_customer_key


def get_active_billing_key(tenant_id: int) -> BillingKey | None:
    """테넌트의 활성 빌링키를 반환한다."""
    return (
        BillingKey.objects.filter(tenant_id=tenant_id, is_active=True)
        .select_related("billing_profile")
        .first()
    )


def list_billing_keys(tenant_id: int) -> QuerySet:
    """테넌트의 모든 빌링키를 반환한다 (최신순)."""
    return (
        BillingKey.objects.filter(tenant_id=tenant_id)
        .select_related("billing_profile")
        .order_by("-created_at")
    )


def issue_billing_key(tenant_id: int, auth_key: str) -> BillingKey:
    """
    새 빌링키를 발급한다.

    1. BillingProfile에서 customerKey 확보
    2. Program 잠금 및 PROCESSING 결제 부재 확인
    3. 잠금을 유지한 채 Toss 발급 후 기존 활성 키 교체

    Raises:
        ValueError: Toss API 호출 실패 시
    """
    # 1. customerKey 확보 (트랜잭션 불필요)
    profile = _get_or_create_billing_profile(tenant_id)

    provider_applied = False
    try:
        try:
            client = _get_client()
        except Exception as exc:
            raise BillingCredentialUnavailable(
                "카드 정보를 안전하게 불러올 수 없습니다. 운영자에게 문의해 주세요."
            ) from exc
        with transaction.atomic():
            _lock_card_mutation_scope(tenant_id)
            profile = BillingProfile.objects.select_for_update().get(pk=profile.pk)
            active_keys = list(
                BillingKey.objects.select_for_update()
                .filter(tenant_id=tenant_id, is_active=True)
                .order_by("id")
            )

            # Keep the Program lock through the provider call. A payment claim
            # cannot start with the old key while replacement is in progress.
            try:
                result = client.issue_billing_key(
                    auth_key=auth_key,
                    customer_key=profile.provider_customer_key,
                )
            except Exception as exc:
                logger.critical(
                    "Billing key issuance outcome unknown: tenant=%s error=%s",
                    tenant_id,
                    type(exc).__name__,
                    exc_info=True,
                )
                raise BillingProviderOutcomeUnknown(
                    "카드 등록 결과를 확인 중입니다. 다시 등록하지 말고 운영자에게 문의해 주세요."
                ) from exc
            if not isinstance(result, Mapping):
                raise BillingProviderOutcomeUnknown(
                    "카드 등록 결과를 확인 중입니다. 다시 등록하지 말고 운영자에게 문의해 주세요."
                )
            if not result.get("success"):
                if result.get("outcome_unknown"):
                    logger.critical(
                        "Billing key issuance outcome unknown: tenant=%s code=%s",
                        tenant_id,
                        result.get("error_code", ""),
                    )
                    raise BillingProviderOutcomeUnknown(
                        "카드 등록 결과를 확인 중입니다. 다시 등록하지 말고 운영자에게 문의해 주세요."
                    )
                error_msg = result.get("error_message", "Unknown error")
                error_code = result.get("error_code", "")
                logger.error(
                    "Billing key issuance failed for tenant %s: [%s] %s",
                    tenant_id, error_code, error_msg,
                )
                raise ValueError(f"빌링키 발급 실패: [{error_code}] {error_msg}")

            provider_applied = True
            try:
                issued_key, card_company, card_number = _validate_issued_billing_key(
                    result,
                    expected_customer_key=profile.provider_customer_key,
                )
            except ValueError as exc:
                raise BillingProviderOutcomeUnknown(
                    "카드 등록 결과를 확인 중입니다. 다시 등록하지 말고 운영자에게 문의해 주세요."
                ) from exc

            now = timezone.now()
            if active_keys:
                BillingKey.objects.filter(
                    id__in=[key.id for key in active_keys]
                ).update(is_active=False, deactivated_at=now)

            billing_key = BillingKey.objects.create(
                tenant_id=tenant_id,
                billing_profile=profile,
                provider="tosspayments",
                billing_key=encrypt_billing_key(issued_key),
                card_company=card_company,
                card_number_masked=card_number,
                is_active=True,
            )
    except BillingCredentialUnavailable as exc:
        _record_card_security_failure(
            tenant_id=tenant_id,
            operation="issue",
            billing_key_id=None,
            stage="client_init",
            error=exc.__cause__ or exc,
        )
        logger.critical(
            "Billing key issuance blocked before provider call: tenant=%s error=%s",
            tenant_id,
            type(exc.__cause__ or exc).__name__,
        )
        raise
    except BillingProviderOutcomeUnknown as exc:
        _record_card_reconciliation_required(
            tenant_id=tenant_id,
            operation="issue",
            error=exc,
        )
        raise
    except IntegrityError as exc:
        if provider_applied:
            logger.critical(
                "Billing key provider issuance succeeded but local persistence failed: tenant=%s error=%s",
                tenant_id,
                type(exc).__name__,
                exc_info=True,
            )
            _record_card_reconciliation_required(
                tenant_id=tenant_id,
                operation="issue",
                error=exc,
            )
            raise BillingProviderOutcomeUnknown(
                "카드 등록 결과를 확인 중입니다. 다시 등록하지 말고 운영자에게 문의해 주세요."
            ) from exc
        # billingkey_one_active_per_tenant 위반 — application-level lock이 정상 동작하면
        # 여기 도달 안 함. 도달 시 동시성/우회 경로 신호로 ValueError로 변환.
        logger.exception("Billing key active uniqueness violated for tenant %s", tenant_id)
        raise ValueError("이미 활성 빌링키가 존재합니다. 잠시 후 다시 시도해 주세요.")
    except Exception as exc:
        if not provider_applied:
            raise
        logger.critical(
            "Billing key provider issuance succeeded but local persistence failed: tenant=%s error=%s",
            tenant_id,
            type(exc).__name__,
            exc_info=True,
        )
        _record_card_reconciliation_required(
            tenant_id=tenant_id,
            operation="issue",
            error=exc,
        )
        raise BillingProviderOutcomeUnknown(
            "카드 등록 결과를 확인 중입니다. 다시 등록하지 말고 운영자에게 문의해 주세요."
        ) from exc

    logger.info(
        "Billing key issued for tenant %s: %s (%s)",
        tenant_id, billing_key.card_number_masked, billing_key.card_company,
    )
    return billing_key


def delete_billing_key(billing_key_id: int) -> bool:
    """
    빌링키를 삭제한다.

    Program → PROCESSING transaction → BillingKey 순으로 잠근 뒤 Toss에 요청한다.
    결제 claim도 Program을 먼저 잠그므로 삭제와 결제가 서로 추월할 수 없다.

    Returns:
        True if successfully deleted, False if Toss API failed.
    """
    tenant_id = (
        BillingKey.objects.filter(id=billing_key_id, is_active=True)
        .values_list("tenant_id", flat=True)
        .first()
    )
    if tenant_id is None:
        logger.warning("Billing key %s not found or already inactive", billing_key_id)
        return False

    provider_applied = False
    try:
        try:
            client = _get_client()
        except Exception as exc:
            raise BillingCredentialUnavailable(
                "카드 정보를 안전하게 불러올 수 없습니다. 운영자에게 문의해 주세요."
            ) from exc
        with transaction.atomic():
            _lock_card_mutation_scope(tenant_id)
            try:
                bk = BillingKey.objects.select_for_update().get(
                    id=billing_key_id,
                    tenant_id=tenant_id,
                    is_active=True,
                )
            except BillingKey.DoesNotExist:
                logger.warning("Billing key %s already deactivated", billing_key_id)
                return False

            try:
                provider_billing_key = decrypt_billing_key(bk.billing_key)
            except BillingKeyCryptoError as exc:
                raise BillingCredentialUnavailable(
                    "카드 정보를 안전하게 불러올 수 없습니다. 운영자에게 문의해 주세요."
                ) from exc

            try:
                result = client.delete_billing_key(provider_billing_key)
            except Exception as exc:
                logger.critical(
                    "Billing key deletion outcome unknown: tenant=%s key_id=%s error=%s",
                    tenant_id,
                    billing_key_id,
                    type(exc).__name__,
                    exc_info=True,
                )
                raise BillingProviderOutcomeUnknown(
                    "카드 삭제 결과를 확인 중입니다. 다시 삭제하지 말고 운영자에게 문의해 주세요."
                ) from exc
            if not isinstance(result, Mapping):
                raise BillingProviderOutcomeUnknown(
                    "카드 삭제 결과를 확인 중입니다. 다시 삭제하지 말고 운영자에게 문의해 주세요."
                )
            if not result.get("success"):
                if result.get("outcome_unknown"):
                    logger.critical(
                        "Billing key deletion outcome unknown: tenant=%s key_id=%s code=%s",
                        tenant_id,
                        billing_key_id,
                        result.get("error_code", ""),
                    )
                    raise BillingProviderOutcomeUnknown(
                        "카드 삭제 결과를 확인 중입니다. 다시 삭제하지 말고 운영자에게 문의해 주세요."
                    )
                error_msg = result.get("error_message", "Unknown error")
                error_code = result.get("error_code", "")
                logger.error(
                    "Billing key deletion failed at Toss for key %s: [%s] %s",
                    billing_key_id, error_code, error_msg,
                )
                return False

            provider_applied = True
            bk.is_active = False
            bk.deactivated_at = timezone.now()
            bk.save(update_fields=["is_active", "deactivated_at", "updated_at"])
    except BillingCredentialUnavailable as exc:
        _record_card_security_failure(
            tenant_id=tenant_id,
            operation="delete",
            billing_key_id=billing_key_id,
            stage=(
                "decrypt"
                if isinstance(exc.__cause__, BillingKeyCryptoError)
                else "client_init"
            ),
            error=exc.__cause__ or exc,
        )
        logger.critical(
            "Billing key deletion blocked before provider call: tenant=%s key_id=%s error=%s",
            tenant_id,
            billing_key_id,
            type(exc.__cause__ or exc).__name__,
        )
        raise
    except BillingProviderOutcomeUnknown as exc:
        _record_card_reconciliation_required(
            tenant_id=tenant_id,
            operation="delete",
            billing_key_id=billing_key_id,
            error=exc,
        )
        raise
    except Exception as exc:
        if not provider_applied:
            raise
        logger.critical(
            "Billing key provider deletion succeeded but local persistence failed: tenant=%s key_id=%s error=%s",
            tenant_id,
            billing_key_id,
            type(exc).__name__,
            exc_info=True,
        )
        _record_card_reconciliation_required(
            tenant_id=tenant_id,
            operation="delete",
            billing_key_id=billing_key_id,
            error=exc,
        )
        raise BillingProviderOutcomeUnknown(
            "카드 삭제 결과를 확인 중입니다. 다시 삭제하지 말고 운영자에게 문의해 주세요."
        ) from exc

    logger.info("Billing key %s deleted for tenant %s", billing_key_id, tenant_id)

    return True
