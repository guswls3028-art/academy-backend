from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from apps.core.models import ProductUsageDailyActor, ProductUsageEvent
from apps.core.product_analytics.constants import (
    EVENT_CTA_CLICK,
    EVENT_CTA_IMPRESSION,
    EVENT_SCREEN_ENGAGED,
    EVENT_SCREEN_VIEW,
    EVENT_TASK_FAILURE,
    EVENT_TASK_START,
    EVENT_TASK_SUCCESS,
)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def build_overview(
    *,
    days: int,
    tenant_id: int | None,
    role: str,
    surface: str,
) -> dict:
    end_day = timezone.localdate()
    start_day = end_day - timedelta(days=days - 1)
    qs = ProductUsageDailyActor.objects.filter(
        day__gte=start_day,
        day__lte=end_day,
        synthetic=False,
        is_impersonated=False,
    )
    if tenant_id is not None:
        qs = qs.filter(tenant_id=tenant_id)
    if role:
        qs = qs.filter(role=role)
    if surface:
        qs = qs.filter(surface=surface)

    active_actors = qs.values("actor_hash").distinct().count()
    suppressed = tenant_id is not None and 0 < active_actors < 5

    totals = qs.aggregate(
        screen_views=Sum("count", filter=Q(event_type=EVENT_SCREEN_VIEW)),
        screen_engaged=Sum("count", filter=Q(event_type=EVENT_SCREEN_ENGAGED)),
        task_starts=Sum("count", filter=Q(event_type=EVENT_TASK_START)),
        task_successes=Sum("count", filter=Q(event_type=EVENT_TASK_SUCCESS)),
        task_failures=Sum("count", filter=Q(event_type=EVENT_TASK_FAILURE)),
        last_observed_at=Max("last_at"),
    )
    screen_views = totals["screen_views"] or 0
    screen_engaged = totals["screen_engaged"] or 0
    task_starts = totals["task_starts"] or 0
    task_successes = totals["task_successes"] or 0
    task_failures = totals["task_failures"] or 0

    features = []
    ctas = []
    roles = []
    if not suppressed:
        feature_rows = (
            qs.values("feature_id")
            .annotate(
                unique_actors=Count("actor_hash", distinct=True),
                visits=Sum("count", filter=Q(event_type=EVENT_SCREEN_VIEW)),
                engaged=Sum("count", filter=Q(event_type=EVENT_SCREEN_ENGAGED)),
                starts=Sum("count", filter=Q(event_type=EVENT_TASK_START)),
                successes=Sum("count", filter=Q(event_type=EVENT_TASK_SUCCESS)),
                failures=Sum("count", filter=Q(event_type=EVENT_TASK_FAILURE)),
                last_observed_at=Max("last_at"),
            )
            .order_by("-successes", "-engaged", "feature_id")
        )
        for row in feature_rows:
            visits = row["visits"] or 0
            engaged_count = row["engaged"] or 0
            starts = row["starts"] or 0
            successes = row["successes"] or 0
            failures = row["failures"] or 0
            features.append(
                {
                    "feature_id": row["feature_id"],
                    "unique_actors": row["unique_actors"],
                    "visits": visits,
                    "engaged": engaged_count,
                    "engagement_rate": _rate(engaged_count, visits),
                    "starts": starts,
                    "successes": successes,
                    "completion_rate": _rate(successes, starts),
                    "failures": failures,
                    "failure_rate": _rate(failures, starts),
                    "last_observed_at": row["last_observed_at"],
                }
            )

        cta_rows = (
            qs.exclude(cta_id="")
            .values("feature_id", "cta_id", "placement_id", "position_index")
            .annotate(
                impressions=Sum(
                    "count",
                    filter=Q(event_type=EVENT_CTA_IMPRESSION),
                ),
                clicks=Sum("count", filter=Q(event_type=EVENT_CTA_CLICK)),
                unique_actors=Count("actor_hash", distinct=True),
            )
            .order_by("-clicks", "cta_id")
        )
        for row in cta_rows:
            impressions = row["impressions"] or 0
            clicks = row["clicks"] or 0
            ctas.append(
                {
                    **row,
                    "impressions": impressions,
                    "clicks": clicks,
                    "click_rate": _rate(clicks, impressions),
                }
            )

        roles = list(
            qs.values("role")
            .annotate(
                active_actors=Count("actor_hash", distinct=True),
                event_count=Sum("count"),
            )
            .order_by("role")
        )

    raw_qs = ProductUsageEvent.objects.filter(
        occurred_at__date__gte=start_day,
        occurred_at__date__lte=end_day,
    )
    if tenant_id is not None:
        raw_qs = raw_qs.filter(tenant_id=tenant_id)
    quality = raw_qs.aggregate(
        raw_events=Count("id"),
        synthetic_events=Count("id", filter=Q(synthetic=True)),
        impersonated_events=Count("id", filter=Q(is_impersonated=True)),
        last_received_at=Max("received_at"),
    )

    return {
        "period": {
            "days": days,
            "start": start_day,
            "end": end_day,
        },
        "filters": {
            "tenant_id": tenant_id,
            "role": role or None,
            "surface": surface or None,
        },
        "suppressed": suppressed,
        "summary": {
            "active_actors": None if suppressed else active_actors,
            "screen_views": None if suppressed else screen_views,
            "engagement_rate": (
                None if suppressed else _rate(screen_engaged, screen_views)
            ),
            "task_completion_rate": (
                None if suppressed else _rate(task_successes, task_starts)
            ),
            "task_failure_rate": (
                None if suppressed else _rate(task_failures, task_starts)
            ),
            "last_observed_at": (
                None if suppressed else totals["last_observed_at"]
            ),
        },
        "features": features,
        "ctas": ctas,
        "roles": roles,
        "quality": {
            key: (value or 0) if key != "last_received_at" else value
            for key, value in quality.items()
        },
    }
