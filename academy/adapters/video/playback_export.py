"""Convert a canonical Academy HLS rendition to a downloadable MP4 without re-encoding."""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

from academy.adapters.storage.r2_objects import video_bucket_client

MAX_PLAYLIST_BYTES = 1024 * 1024
MAX_SEGMENTS = 14_400
MAX_MEDIA_BYTES = 16 * 1024**3
VARIANT = re.compile(r"v[0-9]{1,2}/index\.m3u8")
SEGMENT = re.compile(r"index[0-9]{1,8}\.ts")
MASTER = re.compile(r"tenants/[1-9][0-9]*/video/hls/[1-9][0-9]*/master\.m3u8")


def _playlist(client, bucket: str, key: str) -> list[str]:
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        data = body.read(MAX_PLAYLIST_BYTES + 1)
    finally:
        body.close()
    if len(data) > MAX_PLAYLIST_BYTES:
        raise ValueError("invalid_playlist_size")
    lines = [line.strip() for line in data.decode("utf-8-sig").splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise ValueError("invalid_playlist")
    return lines


def _best_variant(lines: list[str]) -> str:
    candidates: list[tuple[int, str]] = []
    for i, line in enumerate(lines[:-1]):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        match = re.search(r"(?:^|,)BANDWIDTH=([0-9]{1,10})(?:,|$)", line.split(":", 1)[1])
        path = lines[i + 1]
        if match is None or VARIANT.fullmatch(path) is None:
            raise ValueError("unsupported_variant")
        candidates.append((int(match.group(1)), path))
    if not candidates:
        raise ValueError("no_video_variant")
    return max(candidates)[1]


def _segments(lines: list[str]) -> list[tuple[str, float]]:
    seconds = None
    segments: list[tuple[str, float]] = []
    if lines[-1] != "#EXT-X-ENDLIST":
        raise ValueError("playlist_not_complete")
    for line in lines[1:]:
        if line.startswith(("#EXT-X-KEY:", "#EXT-X-MAP:", "#EXT-X-BYTERANGE:", "#EXT-X-DISCONTINUITY")):
            raise ValueError("unsupported_rendition")
        if line.startswith("#EXTINF:"):
            if seconds is not None:
                raise ValueError("missing_segment")
            seconds = float(line.split(":", 1)[1].split(",", 1)[0])
            if not math.isfinite(seconds) or not 0 < seconds <= 60:
                raise ValueError("invalid_segment_duration")
        elif not line.startswith("#"):
            if seconds is None or not SEGMENT.fullmatch(line):
                raise ValueError("invalid_segment")
            segments.append((line, seconds))
            seconds = None
    if (
        seconds is not None or not 1 <= len(segments) <= MAX_SEGMENTS
        or len({path for path, _ in segments}) != len(segments)
    ):
        raise ValueError("invalid_segment_count")
    return segments


def build_playback_mp4(*, bucket: str, master_key: str, output: Path) -> None:
    if not MASTER.fullmatch(master_key):
        raise ValueError("noncanonical_master")
    client = video_bucket_client()
    root = master_key.rpartition("/")[0]
    variant = root + "/" + _best_variant(_playlist(client, bucket, master_key))
    segments = _segments(_playlist(client, bucket, variant))
    duration = sum(seconds for _, seconds in segments)
    prefix = variant.rpartition("/")[0]
    source = output.with_name("source.m3u8")
    local_playlist = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:60"]
    total = 0
    for part, seconds in segments:
        obj = client.get_object(Bucket=bucket, Key=f"{prefix}/{part}")
        body = obj["Body"]
        try:
            length = int(obj.get("ContentLength") or 0)
            if not 0 < length <= MAX_MEDIA_BYTES - total:
                raise ValueError("media_too_large_or_empty")
            copied = 0
            with source.with_name(part).open("wb") as target:
                while chunk := body.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > length:
                        raise ValueError("segment_length_mismatch")
                    target.write(chunk)
            if copied != length:
                raise ValueError("segment_length_mismatch")
            total += copied
            local_playlist.extend((f"#EXTINF:{seconds:.6f},", part))
        finally:
            body.close()
    source.write_text("\n".join([*local_playlist, "#EXT-X-ENDLIST", ""]), encoding="utf-8")
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror",
        "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
        "-c", "copy", "-map_metadata", "-1", "-movflags", "+faststart", str(output),
    ], check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=600)
    info = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=codec_name:format=duration", "-of", "json", str(output),
    ], check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
    data = json.loads(info.stdout)
    measured = float(data.get("format", {}).get("duration") or 0)
    if (
        (data.get("streams") or [{}])[0].get("codec_name") != "h264"
        or not math.isfinite(measured)
        or abs(measured - duration) > max(2, duration * 0.005)
        or not 0 < output.stat().st_size <= MAX_MEDIA_BYTES
    ):
        raise ValueError("incomplete_playback_export")
