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
