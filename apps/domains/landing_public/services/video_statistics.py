"""Tenant-scoped, bounded refresh of derived public YouTube data."""
import logging
import os
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from academy.adapters.youtube_statistics import StatisticsUnavailable, fetch_youtube_statistics
from apps.domains.landing_public.models.video import PublicVideo


logger = logging.getLogger(__name__)
STATISTICS_MAX_AGE = timedelta(days=30)


def refresh_video_statistics(tenant, video_id=None):
    now = timezone.now()
    rows = PublicVideo.objects.filter(tenant=tenant, status="published", published_content__isnull=False)
    if video_id is not None:
        rows = rows.filter(pk=video_id)
    # Provider-derived data expires independently of publisher-authored content.
    rows.filter(youtube_statistics_checked_at__lt=now - STATISTICS_MAX_AGE).update(
        youtube_view_count=None, youtube_published_at=None, youtube_statistics_checked_at=None,
    )
    api_key = os.environ.get("YOUTUBE_DATA_API_KEY", "").strip()
    if not api_key:
        return 0
    with transaction.atomic():
        batch = list(rows.filter(Q(youtube_statistics_retry_at__isnull=True) | Q(youtube_statistics_retry_at__lte=now))
                     .select_for_update(skip_locked=True)
                     .order_by(F("youtube_statistics_checked_at").asc(nulls_first=True), "id")[:50])
        if not batch:
            return 0
        ids = [row.pk for row in batch]
        rows.filter(pk__in=ids).update(youtube_statistics_retry_at=now + timedelta(minutes=5))
    try:
        values = fetch_youtube_statistics([row.youtube_video_id for row in batch], api_key)
    except StatisticsUnavailable:
        logger.warning("Public video statistics refresh unavailable tenant=%s", tenant.pk)
        return 0
    for row in batch:
        value = values.get(row.youtube_video_id)
        # Recheck visibility after the network request: hide/delete races cannot revive a video.
        rows.filter(pk=row.pk, youtube_video_id=row.youtube_video_id).update(
            youtube_view_count=value["view_count"] if value else None,
            youtube_published_at=value["published_at"] if value else None,
            youtube_statistics_checked_at=now,
            youtube_statistics_retry_at=now + timedelta(hours=6),
        )
    return len(batch)


def public_statistics(video):
    checked = video.youtube_statistics_checked_at
    usable = checked is not None and checked >= timezone.now() - STATISTICS_MAX_AGE
    return {
        "youtube_view_count": video.youtube_view_count if usable else None,
        "youtube_published_at": video.youtube_published_at if usable else None,
        "youtube_statistics_checked_at": checked if usable else None,
    }
