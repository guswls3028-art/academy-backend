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
) -> None:
    """
    NotificationLog 1건 생성. Worker에서 직접 ORM 접근 대신 이 함수만 사용.
    """
    from apps.support.messaging.models import NotificationLog

    NotificationLog.objects.create(
        tenant_id=tenant_id,
        success=success,
        amount_deducted=amount_deducted,
        recipient_summary=recipient_summary[:500] if recipient_summary else "",
        template_summary=template_summary[:255] if template_summary else "",
        failure_reason=failure_reason[:500] if failure_reason else "",
    )
