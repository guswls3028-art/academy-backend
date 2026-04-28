# apps/support/messaging/services/queue_service.py
"""
SQS 큐 기반 메시지 발송 — enqueue_sms, is_reservation_cancelled
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


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
    occurrence_key: Optional[str] = None,
) -> bool:
    """
    SMS/알림톡 발송을 SQS에 넣어 워커가 비동기로 발송하도록 함.

    Args:
        tenant_id: 테넌트 ID (워커에서 잔액/PFID 조회)
        to: 수신 번호
        text: 본문
        sender: 발신 번호
        reservation_id: 예약 ID 있으면 워커에서 취소 여부 Double Check 후 발송/스킵
        message_mode: "sms" | "alimtalk"
        alimtalk_replacements: 알림톡 템플릿 치환
        template_id: 알림톡 템플릿 ID (선택)
        event_type: 비즈니스 이벤트 유형 (멱등성 키용, 예: "check_in_complete")
        target_type: 대상 유형 (예: "student")
        target_id: 대상 ID (예: student.id)
        occurrence_key: 이벤트 발생 식별자 (예: "20260328_session_42"). 동일 이벤트 재전송 방지.

    Returns:
        bool: enqueue 성공 여부
    """
    from apps.domains.messaging.sqs_queue import MessagingSQSQueue
    from apps.domains.messaging.policy import can_send_sms, MessagingPolicyError, is_messaging_disabled, check_recipient_allowed, is_messaging_restricted

    # 로컬 테스트용 tenant(9999): 알림톡·문자 없이 기능만 동작 (발송 스킵)
    if is_messaging_disabled(tenant_id):
        logger.info("enqueue_sms skipped: tenant_id=%s is test tenant (messaging disabled)", tenant_id)
        return False

    # 제한 테넌트: 계정 관련(registration/password) 외 메시징 차단
    # 계정 관련 발송은 OWNER_TENANT_ID로 enqueue되므로 여기서 차단되지 않음
    if is_messaging_restricted(tenant_id):
        logger.info("enqueue_sms blocked: tenant_id=%s messaging restricted (account-only)", tenant_id)
        return False

    # Recipient whitelist guard (테스트 모드 시 허용 번호만 발송)
    if not check_recipient_allowed(to):
        logger.info("enqueue_sms blocked: recipient %s not in test whitelist", (to or "")[:4] + "****")
        return False

    mode = (message_mode or "").strip().lower() or "sms"
    if mode not in ("sms", "alimtalk"):
        mode = "sms"

    # SMS 모드: 자체 키 보유 또는 OWNER 테넌트만 허용
    if mode == "sms":
        if not can_send_sms(tenant_id):
            logger.warning(
                "enqueue_sms blocked by policy: tenant_id=%s cannot send SMS (no own credentials, not owner)",
                tenant_id,
            )
            raise MessagingPolicyError(
                "SMS 발송을 위해서는 자체 발송 계정을 연동하거나 운영자에게 문의하세요.",
                reason="sms_not_allowed",
            )

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
        occurrence_key=occurrence_key,
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
