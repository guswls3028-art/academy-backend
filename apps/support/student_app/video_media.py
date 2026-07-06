"""Student-app video media URL helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timezone as datetime_timezone
from typing import Any

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProctoredPlaybackSession:
    token: str | None = None
    session_id: str | None = None
    expires_at: int | None = None


def build_thumbnail_url(video) -> str | None:
    """Build the same thumbnail URL shape used by VideoSerializer."""
    if not video:
        return None

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

        ttl = int(getattr(settings, "CDN_HLS_LIST_URL_TTL_SECONDS", 6 * 3600))
        expires_at = int(timezone.now().timestamp()) + ttl
        signer = CloudflareSignedURL(
            secret=str(secret),
            key_id=str(getattr(settings, "CDN_HLS_SIGNING_KEY_ID", "v1")),
        )
        return signer.build_url(
            cdn_base=cdn,
            path=path,
            expires_at=expires_at,
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


def pick_video_urls(video, request=None) -> tuple[str | None, str | None]:
    """Return the public HLS URL and optional MP4 URL for student playback."""
    from apps.domains.video.views.playback_mixin import VideoPlaybackMixin

    if not hasattr(video, "status") or video.status != video.Status.READY:
        logger.warning(
            "[pick_video_urls] Video %s is not READY (status: %s)",
            getattr(video, "id", None),
            getattr(video, "status", "UNKNOWN"),
        )
        return None, None

    expires_at = int(timezone.now().timestamp()) + (24 * 3600)
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
            "[pick_video_urls] Generated URL for video %s: %s...",
            getattr(video, "id", None),
            hls_url[:200] if hls_url else None,
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


def issue_proctored_playback_session(
    *,
    video,
    enrollment,
    user,
    device_id: str,
) -> ProctoredPlaybackSession:
    """Create the server-side playback session and token for proctored videos."""
    from academy.adapters.db.django import repositories_video as video_repo
    from apps.domains.video.drm import create_playback_token
    from apps.domains.video.models import VideoPlaybackSession
    from apps.domains.video.services.playback_session import (
        init_session_redis,
        issue_session,
    )

    ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))
    ok, session_payload, _error = issue_session(
        student_id=enrollment.student_id,
        device_id=str(device_id),
        ttl_seconds=ttl,
        max_sessions=int(getattr(settings, "VIDEO_MAX_SESSIONS", 9999)),
        max_devices=int(getattr(settings, "VIDEO_MAX_DEVICES", 9999)),
    )
    if not ok or not session_payload:
        return ProctoredPlaybackSession()

    playback_session_id = session_payload["session_id"]
    expires_at = int(session_payload["expires_at"])
    expires_at_dt = timezone.datetime.fromtimestamp(expires_at, tz=datetime_timezone.utc)

    video_repo.playback_session_create(
        video=video,
        enrollment=enrollment,
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

    token = create_playback_token(
        payload={
            "video_id": video.id,
            "enrollment_id": enrollment.id,
            "session_id": playback_session_id,
            "user_id": user.id,
            "student_id": enrollment.student_id,
            "access_mode": "PROCTORED_CLASS",
            "monitoring_enabled": True,
            "pv": int(getattr(video, "policy_version", 1) or 1),
        },
        ttl_seconds=ttl,
    )
    return ProctoredPlaybackSession(
        token=token,
        session_id=playback_session_id,
        expires_at=expires_at,
    )
