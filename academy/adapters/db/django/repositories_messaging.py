"""
Messaging 도메인 DB 기록 — .objects. 접근을 adapters 내부로 한정 (Gate 7).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError
from django.utils import timezone as tz


def create_notification_log(
    tenant_id: int,
    success: bool,
    amount_deducted: Decimal,
    recipient_summary: str,
    template_summary: str = "",
    failure_reason: str = "",
    message_body: str = "",
    message_mode: str = "",
    sqs_message_id: str = "",
    notification_type: str = "",
) -> bool:
    """
    NotificationLog 1건 생성. Worker에서 직접 ORM 접근 대신 이 함수만 사용.

    Returns:
        True: 정상 생성됨
        False: sqs_message_id 기준 중복 (이미 성공 기록 존재) → 생성 안 함
    """
    from apps.support.messaging.models import NotificationLog

    # DB-level dedup: 동일 SQS 메시지에 대해 이미 성공 기록이 있으면 스킵
    if sqs_message_id and success:
        if NotificationLog.objects.filter(
            sqs_message_id=sqs_message_id, success=True
        ).exists():
            return False

    NotificationLog.objects.create(
        tenant_id=tenant_id,
        success=success,
        amount_deducted=amount_deducted,
        recipient_summary=recipient_summary[:500] if recipient_summary else "",
        template_summary=template_summary[:255] if template_summary else "",
        failure_reason=failure_reason[:500] if failure_reason else "",
        message_body=message_body[:2000] if message_body else "",
        message_mode=message_mode[:20] if message_mode else "",
        sqs_message_id=sqs_message_id[:128] if sqs_message_id else "",
        notification_type=notification_type[:30] if notification_type else "",
    )
    return True


def claim_notification_slot(
    tenant_id: int,
    message_mode: str,
    business_idempotency_key: str,
    sqs_message_id: str = "",
    recipient_summary: str = "",
) -> tuple[bool, int | None]:
    """
    Atomic claim: insert a 'processing' row. If unique constraint fails, it's a duplicate.

    Returns:
        (True, log_id): Slot claimed successfully. Proceed to send.
        (False, None): Duplicate. Already claimed/sent by another worker.
    """
    from apps.support.messaging.models import NotificationLog

    if not business_idempotency_key:
        # Legacy message without business key — skip claim, fall through to old path
        return True, None

    try:
        log = NotificationLog.objects.create(
            tenant_id=tenant_id,
            message_mode=message_mode[:20] if message_mode else "",
            business_idempotency_key=business_idempotency_key,
            status="processing",
            claimed_at=tz.now(),
            success=False,
            amount_deducted=Decimal("0"),
            recipient_summary=recipient_summary[:500] if recipient_summary else "",
            sqs_message_id=sqs_message_id[:128] if sqs_message_id else "",
        )
        return True, log.id
    except IntegrityError:
        return False, None


def finalize_notification(
    log_id: int,
    *,
    success: bool,
    amount_deducted: Decimal = Decimal("0"),
    template_summary: str = "",
    failure_reason: str = "",
    message_body: str = "",
    notification_type: str = "",
) -> None:
    """Update a claimed notification slot with final result."""
    from apps.support.messaging.models import NotificationLog

    NotificationLog.objects.filter(id=log_id).update(
        success=success,
        amount_deducted=amount_deducted,
        status="sent" if success else "failed",
        template_summary=template_summary[:255] if template_summary else "",
        failure_reason=failure_reason[:500] if failure_reason else "",
        message_body=message_body[:2000] if message_body else "",
        notification_type=notification_type[:30] if notification_type else "",
    )
