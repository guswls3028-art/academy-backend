# PATH: apps/worker/video_worker/video/transcoder.py

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

from apps.worker.video_worker.utils import ensure_dir, trim_tail

# ffmpeg stderr 진행률 파싱 (time=00:01:23.45 형태)
_RE_TIME = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")

# preset 유지 (순서 중요)
HLS_VARIANTS = [
    {"name": "1", "width": 426, "height": 240, "video_bitrate": "400k", "audio_bitrate": "64k"},
    {"name": "2", "width": 640, "height": 360, "video_bitrate": "800k", "audio_bitrate": "96k"},
    {"name": "3", "width": 1280, "height": 720, "video_bitrate": "2500k", "audio_bitrate": "128k"},
]


class TranscodeError(RuntimeError):
    pass


def _probe_resolution(input_path: str, ffprobe_bin: str, timeout: int) -> tuple[int, int]:
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        input_path,
    ]
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 0, 0

    if p.returncode != 0:
        return 0, 0

    try:
        data = json.loads(p.stdout)
        s = (data.get("streams") or [{}])[0]
        return int(s.get("width") or 0), int(s.get("height") or 0)
    except Exception:
        return 0, 0


def _select_variants(input_w: int, input_h: int) -> List[dict]:
    """
    입력 해상도 상한 반영:
    - 원본보다 큰 variant는 제외
    """
    selected = []
    for v in HLS_VARIANTS:
        if v["width"] <= input_w and v["height"] <= input_h:
            selected.append(v)
    # 안전장치: 최소 1개
    if not selected:
        selected.append(HLS_VARIANTS[0])
    return selected


def prepare_output_dirs(output_root: Path, variants: List[dict]) -> None:
    ensure_dir(output_root)
    for v in variants:
        ensure_dir(output_root / f"v{v['name']}")


def has_audio_stream(*, input_path: str, ffprobe_bin: str, timeout: int) -> bool:
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        input_path,
    ]
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False

    if p.returncode != 0:
        return False

    try:
        data = json.loads(p.stdout)
        streams = data.get("streams") or []
        return any(s.get("codec_type") == "audio" for s in streams)
    except Exception:
        return False


def build_filter_complex(variants: List[dict]) -> str:
    parts: List[str] = []
    split_count = len(variants)
    parts.append("[0:v]split={}".format(split_count) + "".join(f"[v{i}]" for i in range(split_count)))
    for i, v in enumerate(variants):
        parts.append(f"[v{i}]scale={v['width']}:{v['height']}[v{i}out]")
    return ";".join(parts)


def build_ffmpeg_command(
    *,
    input_path: str,
    variants: List[dict],
    with_audio: bool,
    ffmpeg_bin: str,
    hls_time: int,
) -> List[str]:
    cmd: List[str] = [
        ffmpeg_bin,
        "-y",
        "-i", input_path,
        "-filter_complex", build_filter_complex(variants),
    ]

    for i, v in enumerate(variants):
        cmd += ["-map", f"[v{i}out]"]
        if with_audio:
            cmd += ["-map", "0:a?"]

        cmd += [
            f"-c:v:{i}", "libx264",
            "-profile:v", "main",
            "-pix_fmt", "yuv420p",
            f"-b:v:{i}", v["video_bitrate"],
            "-g", "48",
            "-keyint_min", "48",
            "-sc_threshold", "0",
        ]

        if with_audio:
            cmd += [
                f"-c:a:{i}", "aac",
                "-ac", "2",
                f"-b:a:{i}", v["audio_bitrate"],
            ]

    if with_audio:
        var_map = " ".join(f"v:{i},a:{i},name:{v['name']}" for i, v in enumerate(variants))
    else:
        var_map = " ".join(f"v:{i},name:{v['name']}" for i, v in enumerate(variants))

    cmd += [
        "-f", "hls",
        "-hls_time", str(hls_time),
        "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", "v%v/index%d.ts",
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", var_map,
        "v%v/index.m3u8",
    ]
    return cmd


def _parse_time_seconds(line: str) -> Optional[float]:
    """ffmpeg stderr에서 time=HH:MM:SS.ms 추출 후 초 단위로 반환."""
    m = _RE_TIME.search(line)
    if not m:
        return None
    h, m_, s, cs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 3600 + m_ * 60 + s + cs / 100.0


def transcode_to_hls(
    *,
    video_id: int,
    input_path: str,
    output_root: Path,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    hls_time: int,
    timeout: Optional[int],
    duration_sec: Optional[float] = None,
    progress_callback: Optional[Callable[[float, float], None]] = None,
) -> Path:
    # 입력 해상도 기반 variant 선택
    w, h = _probe_resolution(input_path, ffprobe_bin, min(60, int(timeout or 60)))
    variants = _select_variants(w, h)

    prepare_output_dirs(output_root, variants)

    with_audio = has_audio_stream(
        input_path=input_path,
        ffprobe_bin=ffprobe_bin,
        timeout=min(60, int(timeout or 60)),
    )

    cmd = build_ffmpeg_command(
        input_path=input_path,
        variants=variants,
        with_audio=with_audio,
        ffmpeg_bin=ffmpeg_bin,
        hls_time=hls_time,
    )

    use_callback = (
        progress_callback is not None
        and duration_sec is not None
        and duration_sec > 0
    )

    if use_callback:
        total_sec = float(duration_sec)
        last_pct = -1
        stderr_lines: List[str] = []

        def on_progress(current_sec: float) -> None:
            nonlocal last_pct
            pct = int(50 + 35 * (current_sec / total_sec)) if total_sec > 0 else 50
            pct = min(85, max(50, pct))
            if pct != last_pct:
                last_pct = pct
                progress_callback(current_sec, total_sec)

        try:
            p = subprocess.Popen(
                cmd,
                cwd=str(output_root.resolve()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            assert p.stderr is not None
            for line in p.stderr:
                stderr_lines.append(line)
                if len(stderr_lines) > 50:
                    stderr_lines.pop(0)
                t = _parse_time_seconds(line)
                if t is not None:
                    on_progress(t)
            p.wait(timeout=timeout)
            if p.returncode != 0:
                raise TranscodeError(
                    f"ffmpeg failed video_id={video_id} with_audio={with_audio} stderr={trim_tail(''.join(stderr_lines))}"
                )
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            raise TranscodeError(f"ffmpeg timeout video_id={video_id} seconds={timeout}")
        except Exception as e:
            if isinstance(e, TranscodeError):
                raise
            raise TranscodeError(f"ffmpeg error video_id={video_id}: {e}") from e
        stderr_tail = ""
    else:
        try:
            p = subprocess.run(
                cmd,
                cwd=str(output_root.resolve()),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise TranscodeError(f"ffmpeg timeout video_id={video_id} seconds={timeout}") from e

        if p.returncode != 0:
            raise TranscodeError(
                f"ffmpeg failed video_id={video_id} with_audio={with_audio} stderr={trim_tail(p.stderr)}"
            )
        stderr_tail = p.stderr

    master = output_root / "master.m3u8"
    if not master.exists():
        raise TranscodeError("master.m3u8 not created")

    return master
