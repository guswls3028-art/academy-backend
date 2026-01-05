from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List


# ---------------------------------------------------------------------
# HLS Variant Ladder
# ---------------------------------------------------------------------
# ⚠️ name에는 절대 'v' 붙이지 말 것
HLS_VARIANTS = [
    {"name": "1", "width": 426,  "height": 240,  "video_bitrate": "400k",  "audio_bitrate": "64k"},
    {"name": "2", "width": 640,  "height": 360,  "video_bitrate": "800k",  "audio_bitrate": "96k"},
    {"name": "3", "width": 1280, "height": 720,  "video_bitrate": "2500k", "audio_bitrate": "128k"},
]


# ---------------------------------------------------------------------
# Directory preparation
# ---------------------------------------------------------------------
def prepare_output_dirs(output_root: Path) -> None:
    """
    storage/media/hls/videos/{video_id}/
      ├─ master.m3u8
      ├─ v1/
      │   ├─ index.m3u8
      │   └─ index0.ts ...
      ├─ v2/
      └─ v3/
    """
    output_root.mkdir(parents=True, exist_ok=True)

    # v1, v2, v3 디렉토리 미리 생성
    for v in HLS_VARIANTS:
        (output_root / f"v{v['name']}").mkdir(parents=True, exist_ok=True)


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
# ffmpeg command builder (⭐ 표준형)
# ---------------------------------------------------------------------
def build_ffmpeg_command(input_path: str) -> List[str]:
    """
    ⚠️ 모든 출력 경로는 '상대 경로'만 사용
    ⚠️ cwd 기준으로 ffmpeg 실행됨
    """
    cmd: List[str] = [
        "ffmpeg",
        "-y",
        "-i", input_path,  # 입력은 절대 경로여도 OK
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

        # ✅ 표준: 상대 경로 + POSIX 슬래시
        "-hls_segment_filename", "v%v/index%d.ts",
        "-master_pl_name", "master.m3u8",

        "-var_stream_map",
        " ".join(
            f"v:{i},a:{i},name:{v['name']}"
            for i, v in enumerate(HLS_VARIANTS)
        ),

        # ✅ variant playlist도 상대 경로
        "v%v/index.m3u8",
    ]

    return cmd


# ---------------------------------------------------------------------
# Public API (⭐ 표준형 실행부)
# ---------------------------------------------------------------------
def transcode_to_hls(
    *,
    video_id: int,
    input_path: str,
    output_root: Path,
    timeout: int | None = None,
) -> Path:
    """
    Execute HLS transcoding from local mp4 (Best Practice)
    """

    # 1. 출력 디렉토리 준비
    prepare_output_dirs(output_root)

    # 2. ffmpeg 명령어 생성
    cmd = build_ffmpeg_command(input_path)

    # 3. ⭐ 핵심: output_root를 cwd로 실행
    process = subprocess.run(
        cmd,
        cwd=str(output_root.resolve()),
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
