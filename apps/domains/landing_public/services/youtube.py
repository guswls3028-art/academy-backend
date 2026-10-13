"""Bounded metadata lookup; user-supplied URLs are never network destinations."""
import re
from urllib.parse import parse_qs, urlsplit

from rest_framework.exceptions import APIException, ValidationError
from academy.adapters.youtube_oembed import MetadataUnavailable, fetch_youtube_metadata


class YouTubeUnavailable(APIException):
    status_code = 503
    default_detail = "유튜브 영상 정보를 확인하지 못했습니다. 공개 설정을 확인하거나 잠시 후 다시 시도해주세요."
    default_code = "youtube_unavailable"


def normalize_youtube_url(value):
    invalid = "유튜브 영상 링크를 확인해주세요. 일반 영상 또는 Shorts 링크를 입력할 수 있습니다."
    try:
        url = urlsplit(value.strip())
        if url.scheme not in ("https", "http") or url.username or url.password or url.port or len(value) > 2048:
            raise ValueError
        path = url.path.strip("/").split("/")
        video_id = None
        if url.hostname == "youtu.be" and len(path) == 1:
            video_id = path[0]
        elif url.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            if url.path == "/watch":
                ids = parse_qs(url.query).get("v", [])
                video_id = ids[0] if len(ids) == 1 else None
            elif len(path) == 2 and path[0] == "shorts":
                video_id = path[1]
        if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        raise ValidationError({"url": invalid})
    return {
        "youtube_video_id": video_id,
        "canonical_url": f"https://www.youtube.com/watch?v={video_id}",
        "format": "vertical" if path[0] == "shorts" else "landscape",
    }


def resolve_youtube(value):
    result = normalize_youtube_url(value)
    try:
        result.update(fetch_youtube_metadata(result["youtube_video_id"]))
    except MetadataUnavailable:
        raise YouTubeUnavailable()
    return result
