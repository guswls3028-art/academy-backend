"""
Messaging 도메인 DB 기록 — .objects. 접근을 adapters 내부로 한정 (Gate 7).
"""
from __future__ import annotations

from decimal import Decimal


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
    )
    return True
