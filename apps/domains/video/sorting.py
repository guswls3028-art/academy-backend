from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence, TypeVar

T = TypeVar("T")

_NUMBERED_SUFFIX_RE = re.compile(
    r"^(?P<base>.+?)\s*(?:-|–|—)\s*(?P<number>\d{1,6})\s*$"
)


@dataclass(frozen=True)
class NumberedTitle:
    base: str
    base_key: str
    number: int


def parse_numbered_title_suffix(title: str | None) -> NumberedTitle | None:
    match = _NUMBERED_SUFFIX_RE.match((title or "").strip())
    if not match:
        return None
    base = match.group("base").strip()
    if not base:
        return None
    return NumberedTitle(
        base=base,
        base_key=base.casefold(),
        number=int(match.group("number")),
    )


def sort_videos_for_playlist(videos: Iterable[T]) -> list[T]:
    rows = list(videos)
    parsed_by_id: dict[int, NumberedTitle] = {}
    anchors: dict[str, tuple[int, str, int]] = {}

    for index, video in enumerate(rows):
        parsed = parse_numbered_title_suffix(getattr(video, "title", ""))
        if parsed is None:
            continue
        parsed_by_id[id(video)] = parsed
        order = int(getattr(video, "order", 0) or 0)
        title = str(getattr(video, "title", "") or "").casefold()
        video_id = int(getattr(video, "id", 0) or index)
        anchor = (order, title, video_id)
        current = anchors.get(parsed.base_key)
        if current is None or anchor < current:
            anchors[parsed.base_key] = anchor

    def key(video: T) -> tuple:
        parsed = parsed_by_id.get(id(video))
        order = int(getattr(video, "order", 0) or 0)
        title = str(getattr(video, "title", "") or "").casefold()
        video_id = int(getattr(video, "id", 0) or 0)
        if parsed is not None:
            anchor = anchors.get(parsed.base_key, (order, title, video_id))
            return (anchor[0], anchor[1], 0, parsed.number, order, video_id)
        return (order, title, 1, 0, order, video_id)

    return sorted(rows, key=key)


def sort_video_dicts_for_playlist(items: Sequence[dict]) -> list[dict]:
    class DictVideo:
        def __init__(self, payload: dict):
            self.payload = payload
            self.id = payload.get("id") or 0
            self.order = payload.get("order") or 0
            self.title = payload.get("title") or ""

    wrappers = [DictVideo(item) for item in items]
    return [wrapper.payload for wrapper in sort_videos_for_playlist(wrappers)]
