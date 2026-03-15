# PATH: apps/worker/video_worker/video/transcoder.py

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from src.application.video.handler import CancelledError
from apps.worker.video_worker.utils import ensure_dir, trim_tail
import logging

logger = logging.getLogger(__name__)

# ffmpeg stderr 진행률 파싱 (time=00:01:23.45 형태)
_RE_TIME = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
# ffmpeg -progress pipe:1 출력 (out_time_ms=마이크로초)
_RE_OUT_TIME_MS = re.compile(r"out_time_ms=(\d+)")

# ── Encoding Policy (V1.1.0+) ──────────────────────────────────────
# 강의 품질 보존 파이프라인: 태블릿 시청 기준, 필기/수식/화면 텍스트 가독성 최적화.
# - 원본 비율 정확 보존 (강제 스케일링 금지)
# - 원본 해상도 ≤ 1080p면 그대로 유지, 초과 시 1080p 다운스케일
# - CRF 기반 품질 제어 (강의 장면 특성에 따라 비트레이트 자동 조절)
# - 단일 고화질 출력 (불필요한 저화질 variant 제거)
ENCODING_CRF = 20          # 높은 품질 (18=거의 무손실, 23=기본, 28=저화질)
ENCODING_MAXRATE = "8000k"  # 스트리밍 대역폭 상한
ENCODING_BUFSIZE = "12000k" # VBV 버퍼 (maxrate * 1.5)
ENCODING_PRESET = "medium"  # 품질 우선 (slow > medium > fast)
ENCODING_AUDIO_BITRATE = "128k"
MAX_OUTPUT_HEIGHT = 1080    # 출력 해상도 상한
MAX_OUTPUT_WIDTH = 1920


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


def _compute_output_resolution(input_w: int, input_h: int) -> tuple[int, int]:
    """
    원본 비율을 정확히 보존하면서 출력 해상도를 결정.
    - 원본이 1080p 이하면 그대로 유지
    - 초과 시 1080p에 맞춰 다운스케일 (비율 보존)
    - ffmpeg libx264는 짝수 치수 필요 → 2의 배수로 내림
    """
    if input_w <= 0 or input_h <= 0:
        # probe 실패 시 스케일링 없이 원본 그대로 (ffmpeg이 알아서 처리)
        return 0, 0

    # 원본이 상한 이내면 그대로
    if input_w <= MAX_OUTPUT_WIDTH and input_h <= MAX_OUTPUT_HEIGHT:
        # 짝수 보장
        return input_w - (input_w % 2), input_h - (input_h % 2)

    # 비율 보존 다운스케일: width와 height 모두 상한 이내가 되도록
    scale_w = MAX_OUTPUT_WIDTH / input_w
    scale_h = MAX_OUTPUT_HEIGHT / input_h
    scale = min(scale_w, scale_h)

    out_w = int(input_w * scale)
    out_h = int(input_h * scale)
    # 짝수 보장 (내림)
    out_w -= out_w % 2
    out_h -= out_h % 2
    return out_w, out_h


def prepare_output_dirs(output_root: Path, variants: List[dict]) -> None:
    """Legacy: kept for backward compatibility. Use direct ensure_dir for new pipeline."""
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


def build_scale_filter(out_w: int, out_h: int) -> Optional[str]:
    """
    원본 비율 보존 스케일 필터. 스케일링 불필요 시 None 반환.
    out_w, out_h가 0이면 스케일링 안 함 (원본 그대로, 짝수 보장만).
    _compute_output_resolution에서 이미 비율 보존 + 짝수 계산 완료이므로
    여기서는 단순 스케일만 적용.
    """
    if out_w <= 0 or out_h <= 0:
        # probe 실패: 짝수 보장만 (원본 유지)
        return "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    return f"scale={out_w}:{out_h},setsar=1"


def build_ffmpeg_command(
    *,
    input_path: str,
    out_w: int,
    out_h: int,
    with_audio: bool,
    ffmpeg_bin: str,
    hls_time: int,
) -> List[str]:
    """
    CRF 기반 단일 고화질 HLS 출력. 원본 비율 보존.
    """
    cmd: List[str] = [
        ffmpeg_bin,
        "-y",
        "-i", input_path,
    ]

    # 스케일 필터 (필요시에만)
    scale_filter = build_scale_filter(out_w, out_h)
    if scale_filter:
        cmd += ["-vf", scale_filter]

    # 비디오 스트림
    cmd += [
        "-map", "0:v:0",
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level", "4.1",
        "-pix_fmt", "yuv420p",
        "-crf", str(ENCODING_CRF),
        "-maxrate", ENCODING_MAXRATE,
        "-bufsize", ENCODING_BUFSIZE,
        "-preset", ENCODING_PRESET,
        "-g", "48",
        "-keyint_min", "48",
        "-sc_threshold", "0",
    ]

    # 오디오 스트림
    if with_audio:
        cmd += [
            "-map", "0:a?",
            "-c:a", "aac",
            "-ac", "2",
            "-b:a", ENCODING_AUDIO_BITRATE,
        ]

    # HLS 출력 (단일 variant → master playlist은 v1만 참조)
    if with_audio:
        var_map = "v:0,a:0,name:1"
    else:
        var_map = "v:0,name:1"

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
    job_id: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    effective_timeout = _effective_ffmpeg_timeout(duration_sec, timeout)
    # 입력 해상도 probe → 비율 보존 출력 해상도 결정
    input_w, input_h = _probe_resolution(input_path, ffprobe_bin, min(60, effective_timeout))
    out_w, out_h = _compute_output_resolution(input_w, input_h)
    logger.info(
        "[TRANSCODER] Resolution: input=%dx%d → output=%dx%d video_id=%s",
        input_w, input_h, out_w, out_h, video_id,
    )

    # 단일 variant 디렉토리 (v1)
    ensure_dir(output_root)
    ensure_dir(output_root / "v1")

    with_audio = has_audio_stream(
        input_path=input_path,
        ffprobe_bin=ffprobe_bin,
        timeout=min(60, effective_timeout),
    )

    cmd = build_ffmpeg_command(
        input_path=input_path,
        out_w=out_w,
        out_h=out_h,
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
            if job_id and cancel_event:
                from apps.worker.video_worker.current_transcode import set_current
                set_current(p, job_id, cancel_event)
            try:
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

                if cancel_event and cancel_event.is_set():
                    raise CancelledError("Retry requested; ffmpeg SIGTERM sent")
                if p.returncode != 0:
                    raise TranscodeError(
                        f"ffmpeg failed video_id={video_id} with_audio={with_audio} stderr={trim_tail(''.join(stderr_lines))}"
                    )
            finally:
                if job_id and cancel_event:
                    from apps.worker.video_worker.current_transcode import clear_current
                    clear_current()
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
        if job_id and cancel_event:
            from apps.worker.video_worker.current_transcode import set_current
            set_current(p, job_id, cancel_event)
        try:
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

            if cancel_event and cancel_event.is_set():
                raise CancelledError("Retry requested; ffmpeg SIGTERM sent")
            if p.returncode != 0:
                stderr_tail = (p.stderr.read() if p.stderr else "")
                raise TranscodeError(
                    f"ffmpeg failed video_id={video_id} with_audio={with_audio} stderr={trim_tail(stderr_tail)}"
                )
            stderr_tail = (p.stderr.read() if p.stderr else "")
        finally:
            if job_id and cancel_event:
                from apps.worker.video_worker.current_transcode import clear_current
                clear_current()

    master = output_root / "master.m3u8"
    if not master.exists():
        raise TranscodeError("master.m3u8 not created")

    return master
