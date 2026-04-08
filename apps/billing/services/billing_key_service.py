"""
빌링키(카드 등록) 서비스 레이어

규칙:
- 테넌트당 활성 빌링키는 1개만 허용
- 새 빌링키 발급 시 기존 활성 키는 비활성화
- 삭제 시 Toss API 먼저 호출 → 성공해야 로컬 비활성화
- select_for_update()로 동시성 안전 보장
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.billing.adapters.toss_payments import TossPaymentsClient
from apps.billing.models import BillingKey, BillingProfile

logger = logging.getLogger(__name__)


def _get_client() -> TossPaymentsClient:
    return TossPaymentsClient()


def get_or_create_customer_key(tenant_id: int) -> str:
    """
    BillingProfile의 provider_customer_key를 반환한다.
    BillingProfile이 없으면 생성한다.
    """
    profile, _created = BillingProfile.objects.get_or_create(
        tenant_id=tenant_id,
        defaults={"provider": "tosspayments"},
    )
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
    2. Toss API로 빌링키 발급 (트랜잭션 밖 — HTTP 호출 중 DB 락 미점유)
    3. DB 트랜잭션: 기존 활성 키 비활성화 + 새 키 저장

    Raises:
        ValueError: Toss API 호출 실패 시
    """
    # 1. customerKey 확보 (트랜잭션 불필요)
    profile, _created = BillingProfile.objects.get_or_create(
        tenant_id=tenant_id,
        defaults={"provider": "tosspayments"},
    )

    # 2. Toss API 호출 — 트랜잭션 밖에서 (최대 60초 HTTP, DB 락 미점유)
    client = _get_client()
    result = client.issue_billing_key(
        auth_key=auth_key,
        customer_key=profile.provider_customer_key,
    )

    if not result.get("success"):
        error_msg = result.get("error_message", "Unknown error")
        error_code = result.get("error_code", "")
        logger.error(
            "Billing key issuance failed for tenant %s: [%s] %s",
            tenant_id, error_code, error_msg,
        )
        raise ValueError(f"빌링키 발급 실패: [{error_code}] {error_msg}")

    # 3. DB 저장 (트랜잭션 + select_for_update — 짧은 DB 작업만)
    with transaction.atomic():
        now = timezone.now()
        active_keys = (
            BillingKey.objects.select_for_update()
            .filter(tenant_id=tenant_id, is_active=True)
        )
        active_keys.update(is_active=False, deactivated_at=now)

        card_info = result.get("card", {})
        billing_key = BillingKey.objects.create(
            tenant_id=tenant_id,
            billing_profile=profile,
            provider="tosspayments",
            billing_key=result["billingKey"],
            card_company=card_info.get("company", ""),
            card_number_masked=card_info.get("number", ""),
            is_active=True,
        )

    logger.info(
        "Billing key issued for tenant %s: %s (%s)",
        tenant_id, billing_key.card_number_masked, billing_key.card_company,
    )
    return billing_key


def delete_billing_key(billing_key_id: int) -> bool:
    """
    빌링키를 삭제한다.

    1. 로컬 키 조회 (트랜잭션 밖)
    2. Toss API로 빌링키 삭제 요청 (HTTP, 트랜잭션 밖)
    3. 성공 시 트랜잭션 내에서 로컬 비활성화
    4. Toss 실패 시 로컬 변경 없음

    Returns:
        True if successfully deleted, False if Toss API failed.
    """
    try:
        bk = BillingKey.objects.get(id=billing_key_id, is_active=True)
    except BillingKey.DoesNotExist:
        logger.warning("Billing key %s not found or already inactive", billing_key_id)
        return False

    # Toss API 호출 — 트랜잭션 밖 (HTTP 60초 타임아웃)
    client = _get_client()
    result = client.delete_billing_key(bk.billing_key)

    if not result.get("success"):
        error_msg = result.get("error_message", "Unknown error")
        error_code = result.get("error_code", "")
        logger.error(
            "Billing key deletion failed at Toss for key %s: [%s] %s",
            billing_key_id, error_code, error_msg,
        )
        return False

    # 로컬 비활성화 — 트랜잭션 내 (짧은 DB 작업)
    with transaction.atomic():
        # 재조회 + lock (동시 삭제 방지)
        updated = BillingKey.objects.filter(
            id=billing_key_id, is_active=True
        ).update(is_active=False, deactivated_at=timezone.now())

    if updated:
        logger.info("Billing key %s deleted for tenant %s", billing_key_id, bk.tenant_id)
    else:
        logger.warning("Billing key %s already deactivated (concurrent delete)", billing_key_id)

    return True
