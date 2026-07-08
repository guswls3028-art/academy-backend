from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_youtube_video_id(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("YouTube URL is required.")

    if YOUTUBE_VIDEO_ID_RE.fullmatch(text):
        return text

    raw = text if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text) else f"https://{text}"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    path_parts = [part for part in parsed.path.split("/") if part]

    def clean(candidate: str | None) -> str | None:
        candidate = (candidate or "").strip()
        if YOUTUBE_VIDEO_ID_RE.fullmatch(candidate):
            return candidate
        return None

    if host == "youtu.be":
        found = clean(path_parts[0] if path_parts else None)
        if found:
            return found

    if host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        query_id = clean((parse_qs(parsed.query).get("v") or [None])[0])
        if query_id:
            return query_id

        if len(path_parts) >= 2 and path_parts[0] in {"embed", "shorts", "live", "v"}:
            found = clean(path_parts[1])
            if found:
                return found

    raise ValueError("올바른 YouTube 영상 링크를 입력해 주세요.")


def youtube_watch_url(video_id: str) -> str:
    video_id = extract_youtube_video_id(video_id)
    return f"https://www.youtube.com/watch?v={video_id}"


def youtube_embed_url(video_id: str) -> str:
    video_id = extract_youtube_video_id(video_id)
    return f"https://www.youtube.com/embed/{video_id}?enablejsapi=1&playsinline=1&rel=0"


def youtube_thumbnail_url(video_id: str) -> str:
    video_id = extract_youtube_video_id(video_id)
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
