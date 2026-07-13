from __future__ import annotations

import hashlib
import re
import uuid

from django.conf import settings
from django.db import migrations
from django.db.models import Q


def _business_key(*, tenant_id: int, payload: dict) -> str:
    owner_id = int(settings.OWNER_TENANT_ID)
    source_id = payload.get("source_tenant_id")
    if source_id is None and tenant_id != owner_id:
        source_id = tenant_id
    source_part = "" if source_id is None else str(int(source_id))
    mode = str(payload.get("message_mode") or "alimtalk").strip().lower()
    if mode not in ("sms", "alimtalk"):
        mode = "alimtalk"
    canonical = (
        f"msg:{owner_id}:{source_part}:{mode}:"
        f"{payload.get('event_type') or 'manual_send'}:"
        f"{payload.get('target_type') or ''}:{payload.get('target_id') or ''}:"
        f"{payload.get('to') or ''}:{payload.get('occurrence_key') or ''}:"
        f"{payload.get('template_id') or ''}"
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _terminal_payload(payload: dict) -> dict:
    redacted = {"redacted": True}
    for key in (
        "tenant_id",
        "source_tenant_id",
        "event_type",
        "target_type",
        "target_id",
        "occurrence_key",
        "message_mode",
        "template_id",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            redacted[key] = value
    target_id = str(redacted.get("target_id") or "")
    legacy_parent_target = re.fullmatch(r"(parent:[^:]+):([^:]+)", target_id)
    if legacy_parent_target and 10 <= len(
        re.sub(r"\D", "", legacy_parent_target.group(2))
    ) <= 15:
        redacted["target_id"] = legacy_parent_target.group(1)
    return redacted


def backfill_delivery_state(apps, schema_editor):
    database = schema_editor.connection.alias
    notification_log = apps.get_model("messaging", "NotificationLog")
    scheduled_notification = apps.get_model("messaging", "ScheduledNotification")

    # Preflight before any write.  Replacing a historical scalar/list payload
    # with {} would destroy forensic evidence and produce a non-deliverable row.
    malformed_ids = []
    for notification_id, payload in (
        scheduled_notification.objects.using(database)
        .order_by("id")
        .values_list("id", "payload")
        .iterator(chunk_size=500)
    ):
        if not isinstance(payload, dict):
            malformed_ids.append(notification_id)
            if len(malformed_ids) >= 20:
                break
    if malformed_ids:
        raise RuntimeError(
            "messaging_delivery_state_malformed_payload:"
            f"ids={malformed_ids}:report_limit=20"
        )

    while True:
        log_ids = list(
            notification_log.objects.using(database)
            .filter(success=False, status="sent")
            .order_by("id")
            .values_list("id", flat=True)[:500]
        )
        if not log_ids:
            break
        notification_log.objects.using(database).filter(id__in=log_ids).update(
            status="failed"
        )

    while True:
        notifications = list(
            scheduled_notification.objects.using(database)
            .filter(Q(dispatch_key__isnull=True) | Q(business_idempotency_key=""))
            .only(
                "id",
                "tenant_id",
                "payload",
                "dispatch_key",
                "business_idempotency_key",
            )
            .order_by("id")[:500]
        )
        if not notifications:
            break
        for notification in notifications:
            dispatch_key = notification.dispatch_key or uuid.uuid4()
            if not isinstance(notification.payload, dict):
                # A rolling old-binary insert can race the preflight.  Preserve
                # it losslessly and stop rather than silently coercing it.
                raise RuntimeError(
                    "messaging_delivery_state_malformed_payload:"
                    f"ids=[{notification.id}]:report_limit=20"
                )
            payload = dict(notification.payload)
            payload["occurrence_key"] = (
                payload.get("occurrence_key") or f"dispatch:{dispatch_key.hex}"
            )
            notification.dispatch_key = dispatch_key
            notification.payload = payload
            notification.business_idempotency_key = _business_key(
                tenant_id=notification.tenant_id,
                payload=payload,
            )
        scheduled_notification.objects.using(database).bulk_update(
            notifications,
            ["dispatch_key", "payload", "business_idempotency_key"],
            batch_size=500,
        )

    # NotificationLog is the operational evidence SSOT. Delivery payloads are
    # needed only while retry remains possible, so scrub historical terminal
    # outbox rows during the rollout migration as well as at runtime.
    last_id = 0
    while True:
        terminal_rows = list(
            scheduled_notification.objects.using(database)
            .filter(
                id__gt=last_id,
                status__in=["sent", "failed", "cancelled"],
            )
            .only("id", "payload")
            .order_by("id")[:500]
        )
        if not terminal_rows:
            break
        for notification in terminal_rows:
            notification.payload = _terminal_payload(notification.payload)
        scheduled_notification.objects.using(database).bulk_update(
            terminal_rows,
            ["payload"],
            batch_size=500,
        )
        last_id = terminal_rows[-1].id


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("messaging", "0033_messaging_delivery_state_machine"),
    ]

    operations = [
        migrations.RunPython(backfill_delivery_state, migrations.RunPython.noop),
    ]
