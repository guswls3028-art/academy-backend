"""Student-app video media URL helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timezone as datetime_timezone
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaybackAccessGrant:
    token: str | None = None
    session_id: str | None = None
    expires_at: int | None = None
    access_mode: str | None = None
    monitoring_enabled: bool = False
    policy_version: int | None = None
    error: str | None = None
    status_code: int = 403


def bounded_inactive_media_expiry(entitlement, *, now=None) -> int:
    """Bound inactive media to the access TTL and optional entitlement expiry."""
    now = now or timezone.now()
    ttl = min(
        600,
        max(1, int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))),
    )
    expires_at = int(now.timestamp()) + ttl
    entitlement_expiry = getattr(entitlement, "expires_at", None)
    if entitlement_expiry is not None:
        expires_at = min(expires_at, int(entitlement_expiry.timestamp()))
    return expires_at


def build_thumbnail_url(video, *, expires_at: int | None = None) -> str | None:
    """Build the same thumbnail URL shape used by VideoSerializer."""
    if not video:
        return None

    try:
        from apps.domains.video.models import Video
        from apps.domains.video.youtube import youtube_thumbnail_url

        if getattr(video, "source_type", None) == Video.SourceType.YOUTUBE:
            video_id = (getattr(video, "youtube_video_id", "") or "").strip()
            return youtube_thumbnail_url(video_id) if video_id else None
    except Exception:
        pass

    cdn = getattr(settings, "CDN_HLS_BASE_URL", None)
    if not cdn:
        return None
    cdn = cdn.rstrip("/")

    def norm(path: str) -> str:
        path = path.lstrip("/")
        if path.startswith("storage/media/"):
            return path[len("storage/"):]
        return path

    def version() -> int:
        try:
            return int(video.updated_at.timestamp())
        except Exception:
            return 0

    def build(rel_path: str) -> str:
        path = "/" + rel_path.lstrip("/")
        version_value = version()
        secret = getattr(settings, "CDN_HLS_SIGNING_SECRET", "") or ""
        if not secret:
            return f"{cdn}{path}?v={version_value}"

        from apps.domains.video.cdn.cloudflare_signing import CloudflareSignedURL

        signed_expires_at = expires_at
        if signed_expires_at is None:
            ttl = int(getattr(settings, "CDN_HLS_LIST_URL_TTL_SECONDS", 6 * 3600))
            signed_expires_at = int(timezone.now().timestamp()) + ttl
        signer = CloudflareSignedURL(
            secret=str(secret),
            key_id=str(getattr(settings, "CDN_HLS_SIGNING_KEY_ID", "v1")),
        )
        return signer.build_url(
            cdn_base=cdn,
            path=path,
            expires_at=signed_expires_at,
            user_id=None,
            extra_query={"v": str(version_value)},
        )

    if getattr(video, "thumbnail", None):
        return build(norm(video.thumbnail.name))

    if getattr(video, "status", None) == video.Status.READY:
        try:
            session = getattr(video, "session", None)
            lecture = getattr(session, "lecture", None) if session else None
            tenant = getattr(lecture, "tenant", None) if lecture else None
            if tenant is None:
                return None
            tenant_id = getattr(tenant, "id", None) or getattr(tenant, "pk", None)
            from apps.core.r2_paths import video_hls_prefix

            return build(
                norm(f"{video_hls_prefix(tenant_id=tenant_id, video_id=video.id)}/thumbnail.jpg")
            )
        except Exception:
            return None

    return None


def pick_video_urls(
    video,
    request=None,
    *,
    expires_at: int | None = None,
) -> tuple[str | None, str | None]:
    """Return the public HLS URL and optional MP4 URL for student playback."""
    from apps.domains.video.views.playback_mixin import VideoPlaybackMixin

    if not hasattr(video, "status") or video.status != video.Status.READY:
        logger.warning(
            "[pick_video_urls] Video %s is not READY (status: %s)",
            getattr(video, "id", None),
            getattr(video, "status", "UNKNOWN"),
        )
        return None, None

    if expires_at is None:
        access_grace_seconds = int(
            getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600)
        )
        try:
            media_duration_seconds = max(0, int(video.duration or 0))
        except (TypeError, ValueError):
            media_duration_seconds = 0
        if media_duration_seconds == 0:
            # Processed media normally has duration metadata. Keep the historical
            # 24-hour ceiling only for legacy READY rows without it.
            media_duration_seconds = max(0, 24 * 60 * 60 - access_grace_seconds)
        expires_at = (
            int(timezone.now().timestamp())
            + media_duration_seconds
            + access_grace_seconds
        )
    user = getattr(request, "user", None) if request else None
    user_id = getattr(user, "id", 0) if user and getattr(user, "is_authenticated", False) else 0

    try:
        tenant_id: Any = None
        try:
            session = getattr(video, "session", None)
            lecture = getattr(session, "lecture", None) if session else None
            tenant_id = getattr(lecture, "tenant_id", None) if lecture else None
        except Exception:
            pass

        logger.info(
            "[pick_video_urls] Generating URL for video %s: hls_path=%s, "
            "file_key=%s, tenant_id=%s, expires_at=%s, user_id=%s",
            getattr(video, "id", None),
            getattr(video, "hls_path", None),
            getattr(video, "file_key", None),
            tenant_id,
            expires_at,
            user_id,
        )

        hls_url = VideoPlaybackMixin()._public_play_url(
            video=video,
            expires_at=expires_at,
            user_id=user_id,
        )
        logger.info(
            "[pick_video_urls] Signed playback URL ready for video %s expires_at=%s",
            getattr(video, "id", None),
            expires_at,
        )
        if not hls_url:
            logger.warning(
                "[pick_video_urls] _public_play_url returned None for video %s",
                getattr(video, "id", None),
            )
            return None, None
    except Exception as exc:
        logger.error(
            "[pick_video_urls] Failed to generate HLS URL for video %s: %s",
            getattr(video, "id", None),
            exc,
            exc_info=True,
        )
        hls_url = None

    return hls_url, None


def issue_playback_access_grant(
    *,
    video,
    enrollment,
    user,
    device_id: str,
    request_id: str | None = None,
) -> PlaybackAccessGrant:
    """Atomically issue current access and a monitored session when required."""
    from academy.adapters.db.django import repositories_video as video_repo
    from academy.application.use_cases.student_video_access_context import (
        lecture_allows_student_learning,
    )
    from apps.domains.enrollment.models import Enrollment
    from apps.domains.lectures.models import Lecture
    from apps.domains.video.drm import create_playback_token
    from apps.domains.video.models import AccessMode, Video, VideoPlaybackSession
    from apps.domains.video.services.access_resolver import get_effective_access_mode
    from apps.domains.video.services.playback_session import (
        get_tenant_session_limits,
        init_session_redis,
        issue_session,
    )

    lecture_id = getattr(getattr(video, "session", None), "lecture_id", None)
    tenant_id = getattr(video, "tenant_id", None) or getattr(enrollment, "tenant_id", None)
    if not lecture_id or not tenant_id:
        return PlaybackAccessGrant(error="access_denied")

    with transaction.atomic():
        lecture = (
            Lecture.objects.select_for_update()
            .filter(id=lecture_id, tenant_id=tenant_id)
            .first()
        )
        if not lecture_allows_student_learning(lecture):
            return PlaybackAccessGrant(error="lecture_inactive")

        locked_enrollment = (
            Enrollment.objects.select_for_update(of=("self",))
            .filter(
                id=enrollment.id,
                tenant_id=tenant_id,
                lecture_id=lecture_id,
                status__in=("ACTIVE", "INACTIVE"),
            )
            .first()
        )
        if locked_enrollment is None:
            return PlaybackAccessGrant(error="enrollment_unavailable")

        current_video = (
            Video.objects.select_for_update(of=("self",))
            .filter(
                id=video.id,
                tenant_id=tenant_id,
                session__lecture_id=lecture_id,
                status=Video.Status.READY,
                deleted_at__isnull=True,
            )
            .first()
        )
        if current_video is None:
            return PlaybackAccessGrant(error="video_not_ready", status_code=404)

        access_mode = get_effective_access_mode(
            video=current_video,
            enrollment=locked_enrollment,
        )
        if access_mode == AccessMode.BLOCKED:
            return PlaybackAccessGrant(error="access_blocked")

        ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))
        if locked_enrollment.status == "INACTIVE":
            from apps.domains.video.services.inactive_entitlements import (
                get_active_inactive_video_entitlement,
            )

            entitlement = get_active_inactive_video_entitlement(
                video=current_video,
                enrollment=locked_enrollment,
            )
            if entitlement is None:
                return PlaybackAccessGrant(error="access_blocked")
            now_timestamp = int(timezone.now().timestamp())
            entitlement_expiry = bounded_inactive_media_expiry(entitlement)
            ttl = min(ttl, entitlement_expiry - now_timestamp)
            if ttl <= 0:
                return PlaybackAccessGrant(error="access_expired")
        monitoring_enabled = access_mode == AccessMode.PROCTORED_CLASS
        playback_session_id = None
        expires_at = int(timezone.now().timestamp()) + ttl
        if monitoring_enabled:
            max_sessions, max_devices = get_tenant_session_limits(lecture.tenant)
            ok, session_payload, error = issue_session(
                student_id=locked_enrollment.student_id,
                device_id=str(device_id),
                ttl_seconds=ttl,
                max_sessions=max_sessions,
                max_devices=max_devices,
            )
            if not ok or not session_payload:
                return PlaybackAccessGrant(
                    access_mode=access_mode.value,
                    monitoring_enabled=True,
                    policy_version=int(
                        getattr(current_video, "policy_version", 1) or 1
                    ),
                    error=error or "session_limit_exceeded",
                    status_code=409,
                )

            playback_session_id = session_payload["session_id"]
            expires_at = int(session_payload["expires_at"])
            expires_at_dt = timezone.datetime.fromtimestamp(
                expires_at,
                tz=datetime_timezone.utc,
            )
            video_repo.playback_session_create(
                video=current_video,
                enrollment=locked_enrollment,
                session_id=playback_session_id,
                device_id=str(device_id),
                status=VideoPlaybackSession.Status.ACTIVE,
                started_at=timezone.now(),
                expires_at=expires_at_dt,
                last_seen=timezone.now(),
                violated_count=0,
                total_count=0,
                is_revoked=False,
            )
            init_session_redis(session_id=playback_session_id, ttl_seconds=ttl)

        token_payload = {
            "video_id": current_video.id,
            "enrollment_id": locked_enrollment.id,
            "session_id": playback_session_id,
            "user_id": user.id,
            "student_id": locked_enrollment.student_id,
            "tenant_id": tenant_id,
            "access_mode": access_mode.value,
            "monitoring_enabled": monitoring_enabled,
            "pv": int(getattr(current_video, "policy_version", 1) or 1),
        }
        if request_id:
            token_payload["rid"] = request_id
        token = create_playback_token(payload=token_payload, ttl_seconds=ttl)
        return PlaybackAccessGrant(
            token=token,
            session_id=playback_session_id,
            expires_at=expires_at,
            access_mode=access_mode.value,
            monitoring_enabled=monitoring_enabled,
            policy_version=int(getattr(current_video, "policy_version", 1) or 1),
        )
