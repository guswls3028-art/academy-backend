# apps/support/messaging/scheduled.py
"""
예약/지연 발송 — ScheduledNotification 생성 + 처리.

delay_mode:
  - delay_minutes: 이벤트 발생 후 N분 뒤 발송
  - scheduled_hour: 다음 지정 시각(KST)에 발송 (예: 7 → 오전 7시)
"""

import logging
from datetime import datetime, timedelta, timezone

from django.utils import timezone as dj_tz

logger = logging.getLogger(__name__)

# 한국 시간대 (UTC+9)
KST = timezone(timedelta(hours=9))


def schedule_notification(
    tenant_id: int,
    trigger: str,
    delay_mode: str,
    delay_value: int,
    payload: dict,
) -> None:
    """
    ScheduledNotification 레코드 생성.

    Args:
        tenant_id: 테넌트 ID
        trigger: AutoSendConfig 트리거명
        delay_mode: "delay_minutes" | "scheduled_hour"
        delay_value: 분 수 또는 시각(0-23)
        payload: enqueue_sms kwargs (JSON 직렬화 가능해야 함)
    """
    from apps.support.messaging.models import ScheduledNotification

    now = dj_tz.now()

    if delay_mode == "delay_minutes":
        send_at = now + timedelta(minutes=delay_value)
    elif delay_mode == "scheduled_hour":
        send_at = _next_kst_hour(now, delay_value)
    else:
        logger.warning("schedule_notification: unknown delay_mode=%s, sending immediately", delay_mode)
        from apps.support.messaging.services import enqueue_sms
        enqueue_sms(**payload)
        return

    ScheduledNotification.objects.create(
        tenant_id=tenant_id,
        trigger=trigger,
        send_at=send_at,
        payload=payload,
        status=ScheduledNotification.Status.PENDING,
    )
    logger.info(
        "schedule_notification: created trigger=%s tenant=%s send_at=%s delay_mode=%s delay_value=%s",
        trigger, tenant_id, send_at.isoformat(), delay_mode, delay_value,
    )


def _next_kst_hour(now: datetime, hour: int) -> datetime:
    """
    다음 도래하는 KST 시각 계산.
    예: 현재 KST 10:30, hour=7 → 다음 날 07:00 KST
    예: 현재 KST 05:00, hour=7 → 오늘 07:00 KST
    """
    now_kst = now.astimezone(KST)
    target = now_kst.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now_kst:
        target += timedelta(days=1)
    return target


def process_due_notifications(batch_size: int = 100) -> dict:
    """
    send_at <= now이고 status=pending인 알림을 처리.
    management command에서 주기적으로 호출.

    Returns:
        {"processed": int, "sent": int, "failed": int}
    """
    from django.db import transaction
    from apps.support.messaging.models import ScheduledNotification
    from apps.support.messaging.services import enqueue_sms

    now = dj_tz.now()
    stats = {"processed": 0, "sent": 0, "failed": 0}

    # pending + send_at 도래 건 조회 (SELECT FOR UPDATE SKIP LOCKED로 동시성 안전)
    due = (
        ScheduledNotification.objects
        .filter(status=ScheduledNotification.Status.PENDING, send_at__lte=now)
        .order_by("send_at")
        [:batch_size]
    )

    for notif in due:
        stats["processed"] += 1
        try:
            enqueue_sms(**notif.payload)
            notif.status = ScheduledNotification.Status.SENT
            notif.sent_at = dj_tz.now()
            notif.save(update_fields=["status", "sent_at"])
            stats["sent"] += 1
        except Exception as e:
            logger.error(
                "process_due_notifications: failed notif_id=%s trigger=%s error=%s",
                notif.id, notif.trigger, e,
            )
            notif.status = ScheduledNotification.Status.FAILED
            notif.error_message = str(e)[:500]
            notif.save(update_fields=["status", "error_message"])
            stats["failed"] += 1

    if stats["processed"]:
        logger.info("process_due_notifications: %s", stats)
    return stats
