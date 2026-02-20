# PATH: apps/worker/video_worker/video/transcoder.py

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from apps.worker.video_worker.utils import ensure_dir, trim_tail
import logging

logger = logging.getLogger(__name__)

# ffmpeg stderr 진행률 파싱 (time=00:01:23.45 형태)
_RE_TIME = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
# ffmpeg -progress pipe:1 출력 (out_time_ms=마이크로초)
_RE_OUT_TIME_MS = re.compile(r"out_time_ms=(\d+)")

# 360p + 720p만 사용 (240p 제거로 CPU 부담 감소, 학원 실사용에 충분)
# preset 유지 (순서 중요): v1=360p, v2=720p
HLS_VARIANTS = [
    {"name": "1", "width": 640, "height": 360, "video_bitrate": "800k", "audio_bitrate": "96k"},
    {"name": "2", "width": 1280, "height": 720, "video_bitrate": "2500k", "audio_bitrate": "128k"},
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
        "-progress", "pipe:1",
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


# Duration-based initial timeout. 작업 중 300초마다 연장되므로 실제 대기 시간은 이 값보다 길 수 있음.
FFMPEG_TIMEOUT_MIN_SECONDS = 3600   # 초기 최소 1h
FFMPEG_TIMEOUT_MAX_SECONDS = 21600  # 초기 상한 6h
FFMPEG_TIMEOUT_DURATION_MULTIPLIER = 2.0
# 작업 중 연장: 300초마다 300초 추가, 최대 24h까지
FFMPEG_CHUNK_SECONDS = 300    # 한 번에 기다리는 구간
FFMPEG_EXTEND_SECONDS = 300   # 연장 시 추가 시간
FFMPEG_MAX_TOTAL_SECONDS = 86400  # 24h 절대 상한 (hang 감지용)


def _effective_ffmpeg_timeout(duration_sec: Optional[float], config_timeout: Optional[int]) -> int:
    """timeout = max(MIN, int(duration * multiplier)), capped at 6h. SQS visibility_timeout must be >= this."""
    if duration_sec is not None and duration_sec > 0:
        from_duration = int(duration_sec * FFMPEG_TIMEOUT_DURATION_MULTIPLIER)
        return min(
            FFMPEG_TIMEOUT_MAX_SECONDS,
            max(FFMPEG_TIMEOUT_MIN_SECONDS, from_duration),
        )
    return int(config_timeout or 3600)


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
    effective_timeout = _effective_ffmpeg_timeout(duration_sec, timeout)
    # 입력 해상도 기반 variant 선택 (probe는 짧은 제한)
    w, h = _probe_resolution(input_path, ffprobe_bin, min(60, effective_timeout))
    variants = _select_variants(w, h)

    prepare_output_dirs(output_root, variants)

    with_audio = has_audio_stream(
        input_path=input_path,
        ffprobe_bin=ffprobe_bin,
        timeout=min(60, effective_timeout),
    )

    cmd = build_ffmpeg_command(
        input_path=input_path,
        variants=variants,
        with_audio=with_audio,
        ffmpeg_bin=ffmpeg_bin,
        hls_time=hls_time,
    )

    # Always use Popen+stderr when duration is known so progress is parsed (no 50% stick). Callback optional.
    use_popen = duration_sec is not None and duration_sec > 0

    if use_popen:
        total_sec = float(duration_sec)
        last_pct = -1
        stderr_lines: List[str] = []

        def on_progress(current_sec: float) -> None:
            nonlocal last_pct
            pct = int(50 + 35 * (current_sec / total_sec)) if total_sec > 0 else 50
            pct = min(85, max(50, pct))
            if pct != last_pct:
                last_pct = pct
                if progress_callback is not None:
                    progress_callback(current_sec, total_sec)

        try:
            logger.info("[TRANSCODER] Starting ffmpeg for video_id=%s cmd=%s", video_id, " ".join(cmd[:5]) + "...")
            p = subprocess.Popen(
                cmd,
                cwd=str(output_root.resolve()),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            assert p.stdout is not None
            assert p.stderr is not None

            def read_stdout_progress() -> None:
                """-progress pipe:1: out_time_ms= 마이크로초 파싱. stderr 버퍼링 없이 진행률 수신."""
                progress_count = 0
                for line in p.stdout or []:
                    m = _RE_OUT_TIME_MS.search(line)
                    if m:
                        current_sec = int(m.group(1)) / 1_000_000.0
                        progress_count += 1
                        if progress_count % 30 == 1:
                            logger.debug("[TRANSCODER] Progress (pipe:1) video_id=%s current=%.1fs", video_id, current_sec)
                        on_progress(current_sec)
                logger.info("[TRANSCODER] Progress pipe finished video_id=%s progress_updates=%d", video_id, progress_count)

            def read_stderr() -> None:
                for line in p.stderr or []:
                    stderr_lines.append(line)
                    if len(stderr_lines) > 50:
                        stderr_lines.pop(0)
                logger.info("[TRANSCODER] Stderr reading finished video_id=%s lines=%d", video_id, len(stderr_lines))

            progress_reader = threading.Thread(target=read_stdout_progress, daemon=True)
            stderr_reader = threading.Thread(target=read_stderr, daemon=True)
            progress_reader.start()
            stderr_reader.start()
            # 작업 중 연장: 300초마다 대기, 타임아웃 시 300초 추가 (최대 24h)
            deadline = time.monotonic() + effective_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    p.kill()
                    p.wait()
                    raise TranscodeError(
                        f"ffmpeg timeout video_id={video_id} (extending, cap 24h) exceeded"
                    )
                chunk = min(FFMPEG_CHUNK_SECONDS, int(remaining))
                try:
                    p.wait(timeout=chunk)
                    break
                except subprocess.TimeoutExpired:
                    deadline += FFMPEG_EXTEND_SECONDS
                    cap = time.monotonic() + FFMPEG_MAX_TOTAL_SECONDS
                    if deadline > cap:
                        deadline = cap
                    logger.debug(
                        "[TRANSCODER] Extended ffmpeg timeout video_id=%s remaining=%ds",
                        video_id, int(deadline - time.monotonic()),
                    )
            progress_reader.join(timeout=2.0)
            stderr_reader.join(timeout=2.0)

            if p.returncode != 0:
                raise TranscodeError(
                    f"ffmpeg failed video_id={video_id} with_audio={with_audio} stderr={trim_tail(''.join(stderr_lines))}"
                )
        except Exception as e:
            if isinstance(e, TranscodeError):
                raise
            raise TranscodeError(f"ffmpeg error video_id={video_id}: {e}") from e
        stderr_tail = ""
    else:
        # duration 미확인 시 Popen + 동일 연장 대기
        p = subprocess.Popen(
            cmd,
            cwd=str(output_root.resolve()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + effective_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                p.kill()
                p.wait()
                raise TranscodeError(
                    f"ffmpeg timeout video_id={video_id} (extending, cap 24h) exceeded"
                )
            chunk = min(FFMPEG_CHUNK_SECONDS, int(remaining))
            try:
                p.wait(timeout=chunk)
                break
            except subprocess.TimeoutExpired:
                deadline += FFMPEG_EXTEND_SECONDS
                cap = time.monotonic() + FFMPEG_MAX_TOTAL_SECONDS
                if deadline > cap:
                    deadline = cap

        if p.returncode != 0:
            stderr_tail = (p.stderr.read() if p.stderr else "")
            raise TranscodeError(
                f"ffmpeg failed video_id={video_id} with_audio={with_audio} stderr={trim_tail(stderr_tail)}"
            )
        stderr_tail = (p.stderr.read() if p.stderr else "")

    master = output_root / "master.m3u8"
    if not master.exists():
        raise TranscodeError("master.m3u8 not created")

    return master
