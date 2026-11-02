"""Limit protected HLS delivery to the currently admitted, tenant-bound student player."""
from datetime import datetime, timezone as dt_timezone
from pathlib import PurePosixPath
from urllib.parse import urlencode
from uuid import UUID

from django.urls import reverse

from apps.domains.video.models import VideoReviewWatchStream
from apps.domains.video.services.review_watch import threshold_ms


def review_stream_url(video, stream, tenant_code):
    master = PurePosixPath(video.hls_path or "master.m3u8").name
    return reverse("review-watch-media", args=[stream, master]) + "?" + urlencode({"tenant": tenant_code})


def renew_review_stream(video, student_id, user_id, key, expires_at, tenant_code):
    try:
        key = UUID(str(key))
    except ValueError:
        return None
    stream = VideoReviewWatchStream.objects.select_for_update().filter(
        key=key, usage__video=video, usage__student_id=student_id, user_id=user_id,
        policy_version=video.policy_version, closed=False,
    ).select_related("usage").first()
    if not stream or stream.usage.used_ms >= threshold_ms(video):
        return None
    stream.access_until = datetime.fromtimestamp(expires_at, tz=dt_timezone.utc)
    stream.save(update_fields=["access_until"])
    if video.source_type == video.SourceType.YOUTUBE:
        from apps.domains.video.youtube import youtube_embed_url
        return youtube_embed_url(video.youtube_video_id)
    return review_stream_url(video, key, tenant_code)
