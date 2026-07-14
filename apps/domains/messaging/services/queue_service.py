# apps/support/messaging/services/queue_service.py
"""
SQS 큐 기반 메시지 발송.

`enqueue_sms`는 기존 public API 이름이며, 실제로는 message_mode에 따라
알림톡만 큐에 넣는다. SMS/LMS 실발송은 정책상 금지되어 있다.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _require_active_business_tenant(tenant_id: int) -> None:
    """Fail closed when the business tenant is gone/inactive; DB errors retry."""
    from apps.core.models import Tenant
    from apps.domains.messaging.policy import MessagingPolicyError

    if not Tenant.objects.filter(pk=tenant_id, is_active=True).exists():
        raise MessagingPolicyError(
            "비활성화되었거나 존재하지 않는 학원에는 알림을 발송할 수 없습니다.",
            reason="business_tenant_inactive",
        )


def enqueue_sms(
    tenant_id: int,
    to: str,
    text: str,
    sender: Optional[str] = None,
    *,
    reservation_id: Optional[int] = None,
    message_mode: Optional[str] = None,
    alimtalk_replacements: Optional[list[dict]] = None,
    template_id: Optional[str] = None,
    event_type: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int | str] = None,
    target_name: Optional[str] = None,
    source_tenant_id: Optional[int] = None,
    occurrence_key: Optional[str] = None,
    source_domain: Optional[str] = None,
    source_use_case: Optional[str] = None,
    domain_object_id: Optional[str] = None,
    actor_id: Optional[int | str] = None,
    trusted_business_tenant_id: Optional[int] = None,
) -> bool:
    """
    알림톡을 SQS에 넣어 워커가 비동기로 발송하도록 함.

    Args:
        tenant_id: 업무 테넌트 ID. 알림톡 큐 payload는 공용 오너 테넌트로 정규화한다.
        to: 수신 번호
        text: 본문
        sender: 발신 번호
        reservation_id: 예약 ID 있으면 워커에서 취소 여부 Double Check 후 발송/스킵
        message_mode: "alimtalk"만 허용 (기본값: alimtalk)
        alimtalk_replacements: 알림톡 템플릿 치환
        template_id: 알림톡 템플릿 ID (선택)
        event_type: 비즈니스 이벤트 유형 (멱등성 키용, 예: "check_in_complete")
        target_type: 대상 유형 (예: "student")
        target_id: 대상 ID (예: student.id)
        source_tenant_id: 오너 대리발송일 때 실제 업무 테넌트 ID
        occurrence_key: 이벤트 발생 식별자 (예: "20260328_session_42"). 동일 이벤트 재전송 방지.
        source_domain/source_use_case/domain_object_id/actor_id: 추적용 발송 원천 메타데이터.

    Returns:
        bool: enqueue 성공 여부
    """
    from apps.domains.messaging.sqs_queue import MessagingSQSQueue
    from apps.domains.messaging.policy import (
        MessagingPolicyError,
        check_recipient_allowed,
        get_owner_tenant_id,
        is_messaging_disabled,
        is_messaging_restricted,
    )

    original_tenant_id = int(tenant_id)
    owner_id = int(get_owner_tenant_id())
    declared_source_tenant_id = (
        int(source_tenant_id) if source_tenant_id is not None else None
    )
    if original_tenant_id != owner_id:
        if (
            declared_source_tenant_id is not None
            and declared_source_tenant_id != original_tenant_id
        ):
            raise MessagingPolicyError(
                "발송 학원과 비용 부담 학원이 일치하지 않습니다.",
                reason="business_tenant_mismatch",
            )
        business_tenant_id = original_tenant_id
    else:
        business_tenant_id = declared_source_tenant_id or owner_id

    if trusted_business_tenant_id is not None:
        if int(trusted_business_tenant_id) != int(business_tenant_id):
            raise MessagingPolicyError(
                "검증된 발송 학원과 비용 부담 학원이 일치하지 않습니다.",
                reason="trusted_business_tenant_mismatch",
            )
    elif original_tenant_id == owner_id and business_tenant_id != owner_id:
        raise MessagingPolicyError(
            "공용 채널 대리발송에는 검증된 업무 학원 정보가 필요합니다.",
            reason="untrusted_owner_proxy",
        )
    policy_tenant_id = int(business_tenant_id)

    if is_messaging_disabled(policy_tenant_id):
        logger.info(
            "enqueue_sms skipped: business_tenant_id=%s messaging disabled",
            policy_tenant_id,
        )
        return False

    # 제한 테넌트: 계정 관련(registration/password) 외 메시징 차단
    # 계정 관련 발송은 OWNER_TENANT_ID로 enqueue되므로 여기서 차단되지 않음
    if is_messaging_restricted(original_tenant_id):
        logger.info("enqueue_sms blocked: tenant_id=%s messaging restricted (account-only)", original_tenant_id)
        return False

    # Recipient whitelist guard (테스트 모드 시 허용 번호만 발송)
    if not check_recipient_allowed(to):
        logger.info("enqueue_sms blocked: recipient %s not in test whitelist", (to or "")[:4] + "****")
        return False

    mode = (message_mode or "").strip().lower() or "alimtalk"
    if mode not in ("sms", "alimtalk"):
        mode = "alimtalk"

    if mode == "sms":
        logger.error("enqueue_sms blocked: SMS/LMS sending is disabled service-wide (tenant_id=%s)", original_tenant_id)
        raise MessagingPolicyError(
            "SMS 발송은 사용하지 않습니다. 공용 알림톡만 발송할 수 있습니다.",
            reason="sms_disabled",
        )

    _require_active_business_tenant(policy_tenant_id)

    if source_tenant_id is None and original_tenant_id != owner_id:
        source_tenant_id = original_tenant_id
    tenant_id = owner_id
    sender = None

    queue = MessagingSQSQueue()
    return queue.enqueue(
        tenant_id=tenant_id,
        to=to,
        text=text,
        sender=sender,
        reservation_id=reservation_id,
        message_mode=mode,
        alimtalk_replacements=alimtalk_replacements,
        template_id=template_id,
        event_type=event_type,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        source_tenant_id=source_tenant_id,
        occurrence_key=occurrence_key,
        source_domain=source_domain,
        source_use_case=source_use_case,
        domain_object_id=domain_object_id,
        actor_id=actor_id,
    )


def build_enqueued_business_key(*, tenant_id: int, payload: dict) -> str:
    """Predict the exact worker business key for a durable outbox payload."""
    from apps.domains.messaging.policy import get_owner_tenant_id
    from apps.domains.messaging.sqs_queue import build_business_idempotency_key

    original_tenant_id = int(tenant_id)
    owner_id = int(get_owner_tenant_id())
    source_tenant_id = payload.get("source_tenant_id")
    if source_tenant_id is None and original_tenant_id != owner_id:
        source_tenant_id = original_tenant_id
    mode = str(payload.get("message_mode") or "alimtalk").strip().lower()
    if mode not in ("sms", "alimtalk"):
        mode = "alimtalk"
    return build_business_idempotency_key(
        tenant_id=owner_id,
        source_tenant_id=(
            int(source_tenant_id) if source_tenant_id is not None else None
        ),
        channel=mode,
        event_type=str(payload.get("event_type") or "manual_send"),
        target_type=str(payload.get("target_type") or ""),
        target_id=str(payload.get("target_id") or ""),
        recipient=str(payload.get("to") or ""),
        occurrence_key=str(payload.get("occurrence_key") or ""),
        template_id=str(payload.get("template_id") or ""),
    )


def is_reservation_cancelled(reservation_id: int, tenant_id=None) -> bool:
    """
    예약 취소 여부 (Double Check용).
    tenant_id가 주어지면 해당 테넌트 소속 예약만 조회(격리).
    tenant_id가 없으면 크로스 테넌트 방지를 위해 항상 False 반환.
    """
    if tenant_id is None:
        logger.warning(
            "is_reservation_cancelled called without tenant_id (reservation_id=%s), "
            "returning False to prevent cross-tenant lookup",
            reservation_id,
        )
        return False
    try:
        from django.apps import apps
        for model in apps.get_models():
            if model.__name__ != "Reservation" or not hasattr(model, "status"):
                continue
            if hasattr(model, "tenant_id"):
                r = model.objects.filter(tenant_id=tenant_id, pk=reservation_id).first()
            else:
                # 모델에 tenant_id 필드 없으면 격리 불가 → 안전하게 False
                continue
            if r and getattr(r, "status", None) == "CANCELLED":
                return True
        return False
    except Exception:
        return False
