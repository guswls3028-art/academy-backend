"""Source media probing helpers for uploaded videos."""

from __future__ import annotations

from typing import Any


def validate_source_media_via_ffprobe(url: str) -> tuple[bool, dict[str, Any], str]:
    """Run ffprobe against a media URL and return minimal upload metadata."""
    if not url:
        return False, {}, "source_url_missing"

    try:
        import ffmpeg  # type: ignore
    except Exception:
        return False, {}, "ffmpeg_module_missing"

    try:
        probe = ffmpeg.probe(url)
    except Exception as e:
        return False, {}, f"ffprobe_failed:{str(e)[:200]}"

    fmt = probe.get("format") or {}
    streams = probe.get("streams") or []

    dur_raw = fmt.get("duration")
    duration = None
    try:
        if dur_raw is not None:
            duration = int(float(dur_raw))
    except Exception:
        duration = None

    has_video = any((s.get("codec_type") == "video") for s in streams)

    if not has_video:
        return False, {"duration": duration, "has_video": False}, "no_video_stream"

    if duration is None:
        return False, {"duration": None, "has_video": True}, "duration_missing"

    if duration < 0:
        return False, {"duration": duration, "has_video": True}, "duration_invalid"

    return True, {"duration": duration, "has_video": True}, ""
