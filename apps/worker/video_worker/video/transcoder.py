# PATH: apps/worker/video_worker/video/transcoder.py

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List


# ---------------------------------------------------------------------
# HLS Variant Ladder
# ---------------------------------------------------------------------
# ⚠️ name에는 절대 'v' 붙이지 말 것
HLS_VARIANTS = [
    {"name": "1", "width": 426, "height": 240, "video_bitrate": "400k", "audio_bitrate": "64k"},
    {"name": "2", "width": 640, "height": 360, "video_bitrate": "800k", "audio_bitrate": "96k"},
    {"name": "3", "width": 1280, "height": 720, "video_bitrate": "2500k", "audio_bitrate": "128k"},
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
# ffprobe: audio stream 존재 여부 확인
# ---------------------------------------------------------------------
def has_audio_stream(input_path: str) -> bool:
    """
    입력 mp4에 audio stream이 존재하는지 확인한다.
    - 오디오가 없으면 var_stream_map에서 a 트랙을 요구하면 ffmpeg가 터짐
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        input_path,
    ]

    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if p.returncode != 0:
        return False

    try:
        data = json.loads(p.stdout)
        streams = data.get("streams") or []
        return any(s.get("codec_type") == "audio" for s in streams)
    except Exception:
        return False


# ---------------------------------------------------------------------
# ffmpeg filter_complex builder
# ---------------------------------------------------------------------
def build_filter_complex() -> str:
    parts: List[str] = []
    split_count = len(HLS_VARIANTS)

    parts.append("[0:v]split={}".format(split_count) + "".join(f"[v{i}]" for i in range(split_count)))

    for i, v in enumerate(HLS_VARIANTS):
        parts.append(f"[v{i}]scale={v['width']}:{v['height']}[v{i}out]")

    return ";".join(parts)


# ---------------------------------------------------------------------
# ffmpeg command builder (⭐ 표준형)
# ---------------------------------------------------------------------
def build_ffmpeg_command(input_path: str, with_audio: bool) -> List[str]:
    """
    ⚠️ 모든 출력 경로는 '상대 경로'만 사용
    ⚠️ cwd 기준으로 ffmpeg 실행됨

    with_audio:
      - True  -> video+audio variant HLS
      - False -> video-only variant HLS (오디오 없는 입력 대응)
    """
    cmd: List[str] = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,  # 입력은 절대 경로여도 OK
        "-filter_complex",
        build_filter_complex(),
    ]

    for i, v in enumerate(HLS_VARIANTS):
        # video map은 항상 존재
        cmd += [
            "-map",
            f"[v{i}out]",
        ]

        # ✅ 오디오가 있을 때만 audio map
        if with_audio:
            cmd += ["-map", "0:a?"]

        # video encoder
        cmd += [
            f"-c:v:{i}",
            "libx264",
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
            f"-b:v:{i}",
            v["video_bitrate"],
            "-g",
            "48",
            "-keyint_min",
            "48",
            "-sc_threshold",
            "0",
        ]

        # ✅ 오디오 있을 때만 aac 인코딩 옵션
        if with_audio:
            cmd += [
                f"-c:a:{i}",
                "aac",
                "-ac",
                "2",
                f"-b:a:{i}",
                v["audio_bitrate"],
            ]

    # ✅ var_stream_map도 오디오 유무에 따라 분기
    if with_audio:
        var_map = " ".join(f"v:{i},a:{i},name:{v['name']}" for i, v in enumerate(HLS_VARIANTS))
    else:
        var_map = " ".join(f"v:{i},name:{v['name']}" for i, v in enumerate(HLS_VARIANTS))

    cmd += [
        "-f",
        "hls",
        "-hls_time",
        "4",
        "-hls_playlist_type",
        "vod",
        "-hls_flags",
        "independent_segments",
        # ✅ 표준: 상대 경로 + POSIX 슬래시
        "-hls_segment_filename",
        "v%v/index%d.ts",
        "-master_pl_name",
        "master.m3u8",
        "-var_stream_map",
        var_map,
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

    ✅ 오디오 없는 입력도 처리 가능:
    - ffprobe로 audio stream 존재 여부 확인
    - 없으면 video-only HLS로 생성
    """
    # 1. 출력 디렉토리 준비
    prepare_output_dirs(output_root)

    # 2. 오디오 스트림 존재 확인
    with_audio = has_audio_stream(input_path)

    # 3. ffmpeg 명령어 생성
    cmd = build_ffmpeg_command(input_path, with_audio=with_audio)

    # 4. ⭐ 핵심: output_root를 cwd로 실행
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
        raise RuntimeError(
            {
                "video_id": video_id,
                "with_audio": with_audio,
                "cmd": cmd,
                "stderr": process.stderr,
            }
        )

    master_path = output_root / "master.m3u8"
    if not master_path.exists():
        raise RuntimeError("master.m3u8 not created")

    return master_path
