"""Public cross-domain entry points owned by the video domain."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

T = TypeVar("T")


def sort_videos_for_playlist(videos: Iterable[T]) -> list[T]:
    from .sorting import sort_videos_for_playlist as _impl

    return _impl(videos)


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
