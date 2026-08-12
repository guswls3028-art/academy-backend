from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone

from apps.core.models import (
    ProductUsageDailyActor,
    ProductUsageEvent,
    Program,
    Tenant,
)
from apps.core.product_analytics.constants import (
    EVENT_TASK_FAILURE,
    EVENT_TASK_START,
    EVENT_TASK_SUCCESS,
)
from apps.core.services.ops_audit import record_audit


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _enabled_tenant_codes() -> list[str]:
    programs = Program.objects.select_related("tenant").filter(
        is_active=True,
        tenant__is_active=True,
    )
    return sorted(
        program.tenant.code
        for program in programs
        if isinstance(program.feature_flags, dict)
        and program.feature_flags.get("product_usage_analytics_enabled") is True
    )


def _postgres_storage(*, observed_days: int, projection_days: int) -> dict:
    result = {
        "raw_relation_bytes": None,
        "daily_relation_bytes": None,
        "database_bytes": None,
        "projected_analytics_bytes": None,
        "projected_database_share": None,
    }
    if connection.vendor != "postgresql":
        return result

    raw_table = ProductUsageEvent._meta.db_table
    daily_table = ProductUsageDailyActor._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_total_relation_size(to_regclass(%s)), "
            "pg_total_relation_size(to_regclass(%s)), "
            "pg_database_size(current_database())",
            [raw_table, daily_table],
        )
        raw_bytes, daily_bytes, database_bytes = cursor.fetchone()

    observed = max(1, observed_days)
    raw_horizon = min(30, projection_days)
    raw_projected = int(raw_bytes * max(1.0, raw_horizon / min(observed, 30)))
    daily_projected = int(
        daily_bytes * max(1.0, projection_days / min(observed, projection_days))
    )
    projected = raw_projected + daily_projected
    return {
        "raw_relation_bytes": raw_bytes,
        "daily_relation_bytes": daily_bytes,
        "database_bytes": database_bytes,
        "projected_analytics_bytes": projected,
        "projected_database_share": (
            round(projected / database_bytes, 6) if database_bytes else None
        ),
    }


def build_pilot_report(
    *,
    tenant_code: str,
    window_days: int = 28,
    projection_days: int = 90,
    max_projected_database_share: float = 0.20,
    db_time_share: float | None = None,
    write_share: float | None = None,
    max_db_time_share: float = 0.10,
    max_write_share: float = 0.10,
) -> dict:
    tenant = Tenant.objects.filter(code=tenant_code, is_active=True).first()
    if tenant is None:
        raise Tenant.DoesNotExist(f"active pilot tenant not found: {tenant_code}")

    now = timezone.now()
    window_start = now - timedelta(days=window_days)
    recent_start = now - timedelta(hours=24)
    pilot_events = ProductUsageEvent.objects.filter(tenant=tenant)
    window_events = pilot_events.filter(received_at__gte=window_start)
    eligible_events = window_events.filter(synthetic=False, is_impersonated=False)

    lifecycle = pilot_events.aggregate(
        first_received_at=Min("received_at"),
        last_received_at=Max("received_at"),
        raw_events=Count("id"),
    )
    first_received_at = lifecycle["first_received_at"]
    observed_days = (
        max(1, (timezone.localdate() - first_received_at.date()).days + 1)
        if first_received_at
        else 0
    )
    quality = window_events.aggregate(
        raw_events=Count("id"),
        synthetic_events=Count("id", filter=Q(synthetic=True)),
        impersonated_events=Count("id", filter=Q(is_impersonated=True)),
        last_received_at=Max("received_at"),
    )
    eligible = eligible_events.aggregate(
        active_actors=Count("actor_hash", distinct=True),
        event_count=Count("id"),
        task_starts=Count("id", filter=Q(event_type=EVENT_TASK_START)),
        task_successes=Count("id", filter=Q(event_type=EVENT_TASK_SUCCESS)),
        task_failures=Count("id", filter=Q(event_type=EVENT_TASK_FAILURE)),
    )
    event_types = {
        row["event_type"]: row["count"]
        for row in eligible_events.values("event_type")
        .annotate(count=Count("id"))
        .order_by("event_type")
    }
    recent_nonpilot_events = (
        ProductUsageEvent.objects.filter(received_at__gte=recent_start)
        .exclude(tenant=tenant)
        .count()
    )
    enabled_codes = _enabled_tenant_codes()
    storage = _postgres_storage(
        observed_days=observed_days,
        projection_days=projection_days,
    )

    warnings: list[str] = []
    breaches: list[str] = []
    auto_disable_reasons: list[str] = []
    if enabled_codes != [tenant_code]:
        breaches.append("enabled_tenant_scope_mismatch")
        auto_disable_reasons.append("enabled_tenant_scope_mismatch")
    if recent_nonpilot_events:
        breaches.append("recent_nonpilot_events")
        auto_disable_reasons.append("recent_nonpilot_events")
    if tenant_code in enabled_codes and not getattr(
        settings, "PRODUCT_ANALYTICS_HASH_KEY", ""
    ):
        breaches.append("analytics_hash_key_missing")
        auto_disable_reasons.append("analytics_hash_key_missing")
    if quality["raw_events"] == 0:
        warnings.append("no_events_in_window")
    elif quality["last_received_at"] and quality["last_received_at"] < recent_start:
        warnings.append("event_ingestion_stale")
    if db_time_share is None or write_share is None:
        warnings.append("db_usage_share_unavailable")
    if db_time_share is not None and db_time_share >= max_db_time_share:
        breaches.append("db_time_share_exceeded")
        auto_disable_reasons.append("db_time_share_exceeded")
    if write_share is not None and write_share >= max_write_share:
        breaches.append("write_share_exceeded")
        auto_disable_reasons.append("write_share_exceeded")
    projected_share = storage["projected_database_share"]
    if (
        projected_share is not None
        and projected_share >= max_projected_database_share
    ):
        breaches.append("projected_database_share_exceeded")
        auto_disable_reasons.append("projected_database_share_exceeded")

    starts = eligible["task_starts"] or 0
    successes = eligible["task_successes"] or 0
    failures = eligible["task_failures"] or 0
    raw_count = quality["raw_events"] or 0
    synthetic_count = quality["synthetic_events"] or 0
    impersonated_count = quality["impersonated_events"] or 0
    return {
        "schema_version": 1,
        "generated_at": now,
        "status": "breach" if breaches else "pass",
        "tenant": {
            "code": tenant_code,
            "id": tenant.id,
            "enabled": tenant_code in enabled_codes,
            "enabled_tenant_codes": enabled_codes,
        },
        "period": {
            "window_days": window_days,
            "projection_days": projection_days,
            "observed_days": observed_days,
            "first_received_at": first_received_at,
            "last_received_at": lifecycle["last_received_at"],
        },
        "quality": {
            "raw_events": raw_count,
            "eligible_events": eligible["event_count"] or 0,
            "active_actors": eligible["active_actors"] or 0,
            "synthetic_events": synthetic_count,
            "synthetic_share": _rate(synthetic_count, raw_count),
            "impersonated_events": impersonated_count,
            "impersonated_share": _rate(impersonated_count, raw_count),
            "recent_nonpilot_events": recent_nonpilot_events,
            "event_types": event_types,
        },
        "tasks": {
            "starts": starts,
            "successes": successes,
            "failures": failures,
            "completion_rate": _rate(successes, starts),
            "failure_rate": _rate(failures, starts),
        },
        "database": {
            **storage,
            "db_time_share": db_time_share,
            "write_share": write_share,
            "max_db_time_share": max_db_time_share,
            "max_write_share": max_write_share,
            "max_projected_database_share": max_projected_database_share,
        },
        "warnings": sorted(set(warnings)),
        "breaches": sorted(set(breaches)),
        "auto_disable_reasons": sorted(set(auto_disable_reasons)),
    }


def disable_pilot_on_hard_breach(*, tenant_code: str, report: dict) -> bool:
    reasons = report.get("auto_disable_reasons") or []
    if not reasons:
        return False
    with transaction.atomic():
        program = (
            Program.objects.select_for_update()
            .select_related("tenant")
            .get(tenant__code=tenant_code, tenant__is_active=True, is_active=True)
        )
        flags = dict(program.feature_flags or {})
        if flags.get("product_usage_analytics_enabled") is not True:
            return False
        flags["product_usage_analytics_enabled"] = False
        program.feature_flags = flags
        program.save(update_fields=["feature_flags"])
        record_audit(
            None,
            action="product_analytics.failsafe_disable",
            summary=f"Product analytics pilot disabled: {tenant_code}",
            target_tenant=program.tenant,
            payload={"reasons": reasons, "schema_version": report["schema_version"]},
        )
    return True
