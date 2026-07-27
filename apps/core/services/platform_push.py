from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.models import PlatformPushOutbox

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
STALE_LOCK_AFTER = timedelta(minutes=15)

_COPY = {
    PlatformPushOutbox.Kind.CONTACT: (
        "새 도입 문의",
        "새로운 도입 문의가 도착했습니다.",
    ),
    PlatformPushOutbox.Kind.BUG: (
        "새 버그 제보",
        "확인이 필요한 버그 제보가 도착했습니다.",
    ),
    PlatformPushOutbox.Kind.FEEDBACK: (
        "새 피드백",
        "새로운 사용자 피드백이 도착했습니다.",
    ),
    PlatformPushOutbox.Kind.INCIDENT: (
        "새 운영 이슈",
        "확인이 필요한 운영 이슈가 감지되었습니다.",
    ),
}


def enqueue_platform_inbox(*, kind: str, item_id: int) -> bool:
    """Enqueue in the caller's transaction; the unique key makes retries safe."""
    if kind not in _COPY:
        raise ValueError(f"Unsupported platform inbox push kind: {kind}")
    _, created = PlatformPushOutbox.objects.get_or_create(
        kind=kind,
        item_id=item_id,
    )
    return created


def build_platform_inbox_payload(*, kind: str, count: int) -> dict:
    title, single_body = _COPY[kind]
    body = single_body if count == 1 else f"새 항목 {count}건이 도착했습니다."
    return {
        "title": title,
        "body": body,
        "url": "/dev/inbox",
        "tag": f"platform-inbox-{kind}",
        "icon": "/tenants/hakwonplus/pwa-192.png",
        "badge": "/tenants/hakwonplus/apple-touch-icon.png",
    }


def _claim_due(*, limit: int) -> list[PlatformPushOutbox]:
    now = timezone.now()
    stale_before = now - STALE_LOCK_AFTER
    with transaction.atomic():
        PlatformPushOutbox.objects.filter(
            status=PlatformPushOutbox.Status.PROCESSING,
            locked_at__lte=stale_before,
        ).update(
            status=PlatformPushOutbox.Status.PENDING,
            locked_at=None,
            next_attempt_at=now,
            last_error="Recovered stale delivery lock.",
        )
        rows = list(
            PlatformPushOutbox.objects.select_for_update(skip_locked=True)
            .filter(
                status=PlatformPushOutbox.Status.PENDING,
                next_attempt_at__lte=now,
            )
            .order_by("created_at")[:limit]
        )
        if rows:
            PlatformPushOutbox.objects.filter(id__in=[row.id for row in rows]).update(
                status=PlatformPushOutbox.Status.PROCESSING,
                locked_at=now,
            )
    return rows


def _mark_sent(rows: list[PlatformPushOutbox]) -> None:
    now = timezone.now()
    PlatformPushOutbox.objects.filter(id__in=[row.id for row in rows]).update(
        status=PlatformPushOutbox.Status.SENT,
        sent_at=now,
        locked_at=None,
        last_error="",
    )


def _reschedule(rows: list[PlatformPushOutbox], error: Exception) -> None:
    now = timezone.now()
    message = error.__class__.__name__[:255]
    for row in rows:
        attempts = row.attempts + 1
        if attempts >= MAX_ATTEMPTS:
            status = PlatformPushOutbox.Status.DEAD
            next_attempt_at = row.next_attempt_at
        else:
            status = PlatformPushOutbox.Status.PENDING
            delay_minutes = min(5 * (2 ** (attempts - 1)), 60)
            next_attempt_at = now + timedelta(minutes=delay_minutes)
        PlatformPushOutbox.objects.filter(id=row.id).update(
            status=status,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            locked_at=None,
            last_error=message,
        )


def dispatch_platform_push_batch(*, limit: int = 200) -> dict[str, int]:
    """Send at most one collapsed notification per kind for this batch."""
    claimed = _claim_due(limit=max(1, min(limit, 1000)))
    grouped: dict[str, list[PlatformPushOutbox]] = defaultdict(list)
    for row in claimed:
        grouped[row.kind].append(row)

    sent_items = 0
    retry_items = 0
    dead_items = 0
    for kind, rows in grouped.items():
        try:
            from apps.domains.teacher_app.push.service import (
                send_push_to_platform_admins,
            )

            send_push_to_platform_admins(
                build_platform_inbox_payload(kind=kind, count=len(rows))
            )
        except Exception as exc:
            logger.warning(
                "Platform push batch deferred: kind=%s count=%s error=%s",
                kind,
                len(rows),
                exc.__class__.__name__,
            )
            _reschedule(rows, exc)
            retry_items += sum(row.attempts + 1 < MAX_ATTEMPTS for row in rows)
            dead_items += sum(row.attempts + 1 >= MAX_ATTEMPTS for row in rows)
        else:
            _mark_sent(rows)
            sent_items += len(rows)

    return {
        "claimed": len(claimed),
        "sent": sent_items,
        "retry": retry_items,
        "dead": dead_items,
        "notifications": len(grouped),
    }
