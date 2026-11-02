"""Limit protected HLS delivery to the currently admitted, tenant-bound student player."""
from datetime import datetime, timezone as dt_timezone
from pathlib import PurePosixPath
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import UUID

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied

from academy.application.use_cases.student_video_access_context import (
    StudentVideoAccessError, resolve_student_video_access_context,
)
from apps.domains.video.models import VideoReviewWatchStream
from apps.domains.video.services.review_watch import threshold_ms


def review_stream_url(video, stream, tenant_code):
    master = PurePosixPath(video.hls_path or "master.m3u8").name
    return reverse("review-watch-media", args=[stream, master]) + "?" + urlencode({"tenant": tenant_code})


def stream_access_is_current(stream, tenant):
    user = get_user_model().objects.filter(pk=stream.user_id, is_active=True).first()
    if not user:
        return False
    # Native HLS requests carry a bearer capability. Reconstruct only the
    # already-admitted principal and child from the server-owned stream.
    principal = SimpleNamespace(tenant=tenant, user=user, META={"HTTP_X_STUDENT_ID": str(stream.usage.student_id)})
    try:
        context = resolve_student_video_access_context(
            principal, stream.usage.video, explicit_enrollment_id=stream.enrollment_id,
            allow_public_enrollment_write=False,
        )
    except (StudentVideoAccessError, PermissionDenied):
        return False
    entitlement = context.direct_entitlement or context.inactive_entitlement
    return (
        context.access_mode_value in (None, "FREE_REVIEW")
        and getattr(context.enrollment, "pk", None) == stream.enrollment_id
        and getattr(entitlement, "pk", None) == stream.entitlement_id
    )


def renew_review_stream(video, student_id, user_id, key, expires_at, tenant_code):
    try:
        key = UUID(str(key))
    except ValueError:
        return None
    stream = VideoReviewWatchStream.objects.select_for_update().filter(
        key=key, usage__video=video, usage__student_id=student_id, user_id=user_id,
        policy_version=video.policy_version, closed=False,
    ).select_related("usage").first()
    if not stream or (video.review_watch_limit_enabled and stream.usage.used_ms >= threshold_ms(video)):
        return None
    stream.access_until = datetime.fromtimestamp(expires_at, tz=dt_timezone.utc)
    stream.save(update_fields=["access_until"])
    if video.source_type == video.SourceType.YOUTUBE:
        from apps.domains.video.youtube import youtube_embed_url
        return youtube_embed_url(video.youtube_video_id)
    return review_stream_url(video, key, tenant_code)
