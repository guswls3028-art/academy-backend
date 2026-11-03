"""Public cross-domain entry points owned by the video domain."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

T = TypeVar("T")


def sort_videos_for_playlist(videos: Iterable[T]) -> list[T]:
    from .sorting import sort_videos_for_playlist as _impl

    return _impl(videos)


def bind_review_token(token, stream, expires_at):
    from .drm import create_playback_token, verify_playback_token

    valid, claims, _ = verify_playback_token(token)
    if not valid or not claims:
        raise ValueError("invalid playback grant")
    return create_playback_token(payload={**claims, "review_stream": stream}, expires_at=expires_at)


def review_stream_url(video, stream, tenant_code):
    from .services.review_media import review_stream_url as _impl

    return _impl(video, stream, tenant_code)


def open_review_stream(video, student, *, user_id, expires_at, context):
    from .services.review_watch import open_review_stream as _impl

    return _impl(video, student, user_id=user_id, expires_at=expires_at, context=context)


def record_review_state(*, video, student, user_id, context, stream_key, sequence, playing, prepare=False, position_ms=0):
    from .services.review_watch import record_review_state as _impl

    return _impl(video=video, student=student, user_id=user_id, context=context, stream_key=stream_key, sequence=sequence,
                 playing=playing, prepare=prepare, position_ms=position_ms)


def youtube_embed_url(video_id: str) -> str:
    from .youtube import youtube_embed_url as _impl

    return _impl(video_id)


def build_effective_playback_policy(*, video, access_mode, permission=None, progress=None) -> dict:
    from .services.playback_policy import build_effective_playback_policy as _impl

    return _impl(
        video=video,
        access_mode=access_mode,
        permission=permission,
        progress=progress,
    )


def consume_video_forward_skip(
    *,
    video,
    enrollment,
    require_inactive_entitlement: bool = False,
    expected_policy_version: int | None = None,
) -> dict:
    from .services.skip_budget import consume_video_forward_skip as _impl

    return _impl(
        video=video,
        enrollment=enrollment,
        require_inactive_entitlement=require_inactive_entitlement,
        expected_policy_version=expected_policy_version,
    )
