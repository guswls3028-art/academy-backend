from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping

from django.conf import settings

from apps.core.models import (
    ProductUsageEvent,
    Program,
    TenantMembership,
)
from apps.core.product_analytics.constants import AUDIENCE_BY_ROLE


def analytics_enabled(tenant) -> bool:
    flags = (
        Program.objects.filter(tenant=tenant, is_active=True)
        .values_list("feature_flags", flat=True)
        .first()
    )
    return bool(
        isinstance(flags, dict)
        and flags.get("product_usage_analytics_enabled") is True
    )


def active_membership(*, tenant, user) -> TenantMembership | None:
    return (
        TenantMembership.objects.filter(
            tenant=tenant,
            user=user,
            is_active=True,
        )
        .only("role")
        .first()
    )


def actor_hash(*, tenant_id: int, user_id: int) -> str:
    key = getattr(settings, "PRODUCT_ANALYTICS_HASH_KEY", "")
    if not key:
        raise RuntimeError("PRODUCT_ANALYTICS_HASH_KEY is not configured")
    message = f"{tenant_id}:{user_id}".encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()


def request_is_impersonated(request) -> bool:
    auth = getattr(request, "auth", None)
    if isinstance(auth, Mapping):
        return bool(auth.get("impersonated_by"))
    getter = getattr(auth, "get", None)
    if callable(getter):
        return bool(getter("impersonated_by"))
    return False


def store_events(
    *,
    tenant,
    user,
    role: str,
    events: list[dict],
    is_impersonated: bool,
) -> tuple[int, int]:
    audience_group = AUDIENCE_BY_ROLE[role]
    hashed_actor = actor_hash(tenant_id=tenant.id, user_id=user.id)

    unique_events: dict[object, dict] = {}
    duplicate_count = 0
    for event in events:
        event_id = event["event_id"]
        if event_id in unique_events:
            duplicate_count += 1
            continue
        unique_events[event_id] = event

    existing_ids = set(
        ProductUsageEvent.objects.filter(
            event_id__in=unique_events,
        ).values_list("event_id", flat=True)
    )
    duplicate_count += len(existing_ids)

    rows = []
    for event_id, event in unique_events.items():
        if event_id in existing_ids:
            continue
        rows.append(
            ProductUsageEvent(
                event_id=event_id,
                tenant=tenant,
                actor_hash=hashed_actor,
                role=role,
                audience_group=audience_group,
                is_impersonated=is_impersonated,
                **{key: value for key, value in event.items() if key != "event_id"},
            )
        )

    ProductUsageEvent.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows), duplicate_count
