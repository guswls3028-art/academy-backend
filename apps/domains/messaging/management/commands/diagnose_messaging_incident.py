"""Privacy-safe, read-only product messaging incident diagnostic."""

from __future__ import annotations

import json
import logging
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.domains.messaging.models import NotificationLog, ScheduledNotification
from apps.domains.messaging.security import (
    build_recipient_fingerprint_candidates,
    normalize_recipient_phone,
)

KST = ZoneInfo("Asia/Seoul")
_SENSITIVE_PROVIDER_LOGGERS = ("httpx", "httpcore", "solapi")


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _counter(values) -> dict[str, int]:
    return dict(sorted(Counter(str(value or "unknown") for value in values).items()))


def _range(qs, field: str) -> dict[str, str | None]:
    first = qs.order_by(field).values_list(field, flat=True).first()
    last = qs.order_by(f"-{field}").values_list(field, flat=True).first()
    return {"first": _iso(first), "last": _iso(last)}


@contextmanager
def _suppress_provider_request_logs():
    """Prevent provider query URLs from leaking the exact recipient."""

    states = []
    for name in _SENSITIVE_PROVIDER_LOGGERS:
        logger = logging.getLogger(name)
        states.append((logger, logger.disabled))
        logger.disabled = True
    try:
        yield
    finally:
        for logger, disabled in states:
            logger.disabled = disabled


def _provider_snapshot(*, recipient: str, since, until) -> dict:
    from apps.domains.messaging.services.solapi_client import get_solapi_client

    client = get_solapi_client()
    if client is None or not hasattr(client, "get_messages"):
        return {"status": "unavailable", "reason": "solapi_client_unavailable"}

    from solapi.model.request.messages.get_messages import GetMessagesRequest

    # The SDK's official examples use calendar-date strings. Passing aware
    # datetimes currently serializes a space-separated value with an offset
    # that the list endpoint rejects.
    start_date = since.astimezone(KST).date().isoformat()
    end_date = until.astimezone(KST).date().isoformat()
    messages = []
    start_key = None
    with _suppress_provider_request_logs():
        for _page in range(10):
            response = client.get_messages(
                GetMessagesRequest(
                    to=recipient,
                    date_type="CREATED",
                    start_date=start_date,
                    end_date=end_date,
                    # Solapi rejects list requests above 500. Keep the
                    # diagnostic paginated at the provider's documented cap.
                    limit=500,
                    start_key=start_key,
                )
            )
            messages.extend((response.message_list or {}).values())
            start_key = response.next_key
            if not start_key:
                break

    def _message_type(message) -> str:
        value = getattr(message, "type", None)
        return str(getattr(value, "value", value) or "unknown")

    disable_sms = []
    for message in messages:
        kakao = getattr(message, "kakao_options", None)
        if kakao is not None:
            disable_sms.append(bool(getattr(kakao, "disable_sms", False)))

    return {
        "status": "ok",
        "total": len(messages),
        "types": _counter(_message_type(message) for message in messages),
        "status_codes": _counter(
            getattr(message, "status_code", "") for message in messages
        ),
        "kakao_disable_sms": {
            "true": sum(1 for value in disable_sms if value),
            "false": sum(1 for value in disable_sms if not value),
        },
        "window": {
            "timezone": "Asia/Seoul",
            "query_start_date": start_date,
            "query_end_date": end_date,
        },
        "truncated": bool(start_key),
    }


class Command(BaseCommand):
    help = (
        "Inspect one tenant's outbox/log/provider evidence without printing phone, "
        "message body, credentials, or provider identifiers."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--recipient", default="")
        parser.add_argument("--origin-id", default="")
        parser.add_argument("--since-hours", type=int, default=72)
        parser.add_argument("--provider", action="store_true")

    def handle(self, *args, **options):
        tenant_id = int(options["tenant_id"])
        recipient = normalize_recipient_phone(options.get("recipient"))
        origin_id = str(options.get("origin_id") or "").strip()
        since_hours = max(1, min(int(options["since_hours"]), 24 * 90))
        until = timezone.now()
        since = until - timedelta(hours=since_hours)

        if options["provider"] and not recipient:
            raise CommandError("--provider requires --recipient")

        fingerprints = (
            build_recipient_fingerprint_candidates(recipient) if recipient else ()
        )
        outboxes = ScheduledNotification.objects.filter(
            tenant_id=tenant_id,
            created_at__gte=since,
        )
        logs = NotificationLog.objects.filter(
            Q(source_tenant_id=tenant_id)
            | Q(source_tenant_id__isnull=True, tenant_id=tenant_id),
            sent_at__gte=since,
        )
        if fingerprints:
            outboxes = outboxes.filter(recipient_fingerprint__in=fingerprints)
            logs = logs.filter(recipient_fingerprint__in=fingerprints)
        if origin_id:
            outboxes = outboxes.filter(origin_id=origin_id)
            logs = logs.filter(origin_id=origin_id)

        outbox_keys = set(
            outboxes.exclude(business_idempotency_key="").values_list(
                "business_idempotency_key",
                flat=True,
            )
        )
        log_keys = set(
            logs.exclude(business_idempotency_key="").values_list(
                "business_idempotency_key",
                flat=True,
            )
        )
        report = {
            "checked_at": _iso(until),
            "filters": {
                "tenant_id": tenant_id,
                "since_hours": since_hours,
                "recipient_filter": bool(recipient),
                "origin_id_filter": bool(origin_id),
            },
            "outbox": {
                "total": outboxes.count(),
                "statuses": _counter(
                    outboxes.values_list("status", flat=True)
                ),
                "triggers": _counter(
                    outboxes.values_list("trigger", flat=True)
                ),
                "origin_types": _counter(
                    outboxes.values_list("origin_type", flat=True)
                ),
                "errors": _counter(
                    outboxes.exclude(error_message="").values_list(
                        "error_message",
                        flat=True,
                    )
                ),
                "time_range": _range(outboxes, "created_at"),
            },
            "delivery_log": {
                "total": logs.count(),
                "statuses": _counter(logs.values_list("status", flat=True)),
                "events": _counter(
                    logs.values_list("notification_type", flat=True)
                ),
                "origin_types": _counter(
                    logs.values_list("origin_type", flat=True)
                ),
                "provider_id_present": logs.exclude(
                    provider_message_id=""
                ).count(),
                "time_range": _range(logs, "sent_at"),
            },
            "linkage": {
                "shared_business_keys": len(outbox_keys & log_keys),
                "outbox_without_log": len(outbox_keys - log_keys),
                "log_without_outbox": len(log_keys - outbox_keys),
            },
        }
        if options["provider"]:
            try:
                report["provider"] = _provider_snapshot(
                    recipient=recipient,
                    since=since,
                    until=until,
                )
            except Exception as exc:
                report["provider"] = {
                    "status": "error",
                    "reason": type(exc).__name__,
                }

        self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))
