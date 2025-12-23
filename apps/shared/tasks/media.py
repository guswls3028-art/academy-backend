# apps/worker/media/video/transcoder.py


from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

from django.conf import settings


# ---------------------------------------------------------------------
# HLS Variant Ladder
# ---------------------------------------------------------------------

HLS_VARIANTS = [
    {"name": "v1", "width": 426, "height": 240, "video_bitrate": "400k", "audio_bitrate": "64k"},
    {"name": "v2", "width": 640, "height": 360, "video_bitrate": "800k", "audio_bitrate": "96k"},
    {"name": "v3", "width": 1280, "height": 720, "video_bitrate": "2500k", "audio_bitrate": "128k"},
]


# ---------------------------------------------------------------------
# Directory preparation
# ---------------------------------------------------------------------

def prepare_output_dirs(output_root: Path) -> None:
    """
    storage/media/hls/videos/{video_id}/
      ├─ v1/
      ├─ v2/
      └─ v3/
    """
    output_root.mkdir(parents=True, exist_ok=True)
    for v in HLS_VARIANTS:
        (output_root / v["name"]).mkdir(exist_ok=True)


# ---------------------------------------------------------------------
# ffmpeg filter_complex builder
# ---------------------------------------------------------------------

def build_filter_complex() -> str:
    parts: List[str] = []
    split_count = len(HLS_VARIANTS)

    parts.append(
        "[0:v]split={}".format(split_count)
        + "".join(f"[v{i}]" for i in range(split_count))
    )

    for i, v in enumerate(HLS_VARIANTS):
        parts.append(f"[v{i}]scale={v['width']}:{v['height']}[v{i}out]")

    return ";".join(parts)


# ---------------------------------------------------------------------
# ffmpeg command builder
# ---------------------------------------------------------------------

def build_ffmpeg_command(input_path: str, output_root: Path) -> List[str]:
    cmd: List[str] = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-filter_complex", build_filter_complex(),
    ]

    for i, v in enumerate(HLS_VARIANTS):
        cmd += [
            "-map", f"[v{i}out]",
            "-map", "0:a?",

            f"-c:v:{i}", "libx264",
            "-profile:v", "main",
            "-pix_fmt", "yuv420p",
            f"-b:v:{i}", v["video_bitrate"],

            "-g", "48",
            "-keyint_min", "48",
            "-sc_threshold", "0",

            f"-c:a:{i}", "aac",
            "-ac", "2",
            f"-b:a:{i}", v["audio_bitrate"],
        ]

    cmd += [
        "-f", "hls",
        "-hls_time", "4",
        "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments",
        "-master_pl_name", "master.m3u8",
        "-var_stream_map",
        " ".join(f"v:{i},a:{i},name:{v['name']}" for i, v in enumerate(HLS_VARIANTS)),
        str(output_root / "v%v" / "index.m3u8"),
    ]

    return cmd


# ---------------------------------------------------------------------
# Public API (실전용)
# ---------------------------------------------------------------------

def transcode_to_hls(
    *,
    video_id: int,
    input_path: str,  # video.file.path
    timeout: int | None = None,
) -> Path:
    """
    Execute HLS transcoding.
    """

    output_root = (
        Path(settings.MEDIA_ROOT)
        / "hls"
        / "videos"
        / str(video_id)
    )

    prepare_output_dirs(output_root)

    cmd = build_ffmpeg_command(
        input_path=input_path,
        output_root=output_root,
    )

    process = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )

    if process.returncode != 0:
        raise RuntimeError({
            "video_id": video_id,
            "cmd": cmd,
            "stderr": process.stderr,
        })

    master_path = output_root / "master.m3u8"
    if not master_path.exists():
        raise RuntimeError("master.m3u8 not created")

    return master_path


# ---------------------------------------------------------------------
# TEST ONLY
# ---------------------------------------------------------------------

def test_local_hls_transcode() -> None:
    input_path = (
        Path(settings.MEDIA_ROOT)
        / "videos"
        / "2025"
        / "12"
        / "19"
        / "던파.mp4"
    )

    transcode_to_hls(
        video_id=2,
        input_path=str(input_path),
    )

    print("✅ HLS transcode completed")
