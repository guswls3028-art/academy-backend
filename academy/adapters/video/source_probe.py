"""Source media probing helpers for uploaded videos."""

from __future__ import annotations

import json
import subprocess
from typing import Any


SOURCE_PROBE_TIMEOUT_SECONDS = 15


def validate_source_media_via_ffprobe(url: str) -> tuple[bool, dict[str, Any], str]:
    """Run ffprobe against a media URL and return minimal upload metadata."""
    if not url:
        return False, {}, "source_url_missing"

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=SOURCE_PROBE_TIMEOUT_SECONDS,
            check=True,
        )
        probe = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return False, {}, "ffprobe_timeout"
    except FileNotFoundError:
        return False, {}, "ffprobe_missing"
    except (subprocess.CalledProcessError, OSError, ValueError):
        return False, {}, "ffprobe_failed"

    if not isinstance(probe, dict):
        return False, {}, "ffprobe_failed"
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
