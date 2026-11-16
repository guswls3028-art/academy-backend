from __future__ import annotations

from collections import deque
import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from academy.application.video import CancelledError
from academy.adapters.video.utils import ensure_dir

logger = logging.getLogger(__name__)

# ffmpeg stderr 진행률 파싱 (time=00:01:23.45 형태)
_RE_TIME = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
# ffmpeg -progress pipe:1 출력 (out_time_ms=마이크로초)
_RE_OUT_TIME_MS = re.compile(r"out_time_ms=(\d+)")

# ── Encoding Policy (V1.1.0+) ──────────────────────────────────────
# 2단계 ABR: 고화질(원본 유지) + 중화질(720p fallback). 비율 정확 보존.
# 태블릿 시청 기준, 필기/수식/화면 텍스트 가독성 최적화.
MAX_OUTPUT_HEIGHT = 1080
MAX_OUTPUT_WIDTH = 1920
MID_HEIGHT = 720

# v2 = 고화질 (원본 해상도 유지, ≤1080p)
# 강의 영상(칠판 글씨, 판서, 화면 캡처) 기준 선명도 최적화.
# CRF 18 + maxrate 12Mbps + preset slow → 텍스트/선 디테일 보존.
HI_CRF = 18
HI_MAXRATE = "12000k"
HI_BUFSIZE = "18000k"
HI_PROFILE = "high"
# The slow preset otherwise uses five references, which pushes 1080p to Level 5.
HI_REFERENCE_FRAMES = 4
HI_AUDIO_BITRATE = "128k"

# v1 = 중화질 (720p, 저속 네트워크 fallback)
# CRF 21 + maxrate 5Mbps → 모바일에서도 칠판 글씨 판독 가능 수준.
MID_CRF = 21
MID_MAXRATE = "5000k"
MID_BUFSIZE = "7500k"
MID_PROFILE = "main"
MID_AUDIO_BITRATE = "128k"

ENCODING_PRESET = "slow"


class TranscodeError(RuntimeError):
    pass


_FFMPEG_DIAGNOSTIC_LINES = 8
_FFMPEG_DIAGNOSTIC_CHARS = 300
_FFMPEG_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s]+", re.IGNORECASE)
_FFMPEG_CREDENTIAL = re.compile(
    r"\b(?:authorization|proxy-authorization|cookie|set-cookie|password|token|signature)\s*[:=].*$",
    re.IGNORECASE,
)
_FFMPEG_PATH = re.compile(r"(?:[a-z]:[\\/]|(?<![\w/])/|\\\\)[^\r\n]*?(?=:\s|$)", re.IGNORECASE)
_NO_INPUT_VIDEO = re.compile(
    r"""^(?:Stream map ['"]0:v:0['"] matches no streams\.?|"""
    r"""Stream specifier ['"]:v['"] in filtergraph description .* matches no streams\.?)$"""
)


class FFmpegProcessError(TranscodeError):
    """A nonzero FFmpeg exit with bounded diagnostics for the Batch log."""

    def __init__(self, exit_code: int, diagnostics: tuple[str, ...], *, invalid_source: bool):
        message = "Source file contains no video stream." if invalid_source else f"FFmpeg exited with code {exit_code}."
        super().__init__(message)
        self.exit_code = exit_code
        self.diagnostics = diagnostics
        self.invalid_source = invalid_source


def _probe_resolution(input_path: str, ffprobe_bin: str, timeout: int) -> tuple[int, int]:
    """
    원본 해상도 + 회전 메타데이터 반영.
    휴대폰 가로 촬영 시 센서는 세로(1080×1920)이고 rotation=90으로 가로 표시.
    side_data_list의 rotation 또는 stream tags의 rotate 값을 읽어 90/270도면 w↔h 스왑.
    """
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:stream_side_data=rotation:stream_tags=rotate",
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
        w = int(s.get("width") or 0)
        h = int(s.get("height") or 0)

        # 회전 감지: side_data_list 또는 tags.rotate
        rotation = 0
        for sd in (s.get("side_data_list") or []):
            if "rotation" in sd:
                rotation = abs(int(float(sd["rotation"])))
                break
        if rotation == 0:
            tags = s.get("tags") or {}
            rot_str = tags.get("rotate") or tags.get("ROTATE") or "0"
            rotation = abs(int(float(rot_str)))

        # 90도 또는 270도 회전이면 가로세로 스왑
        if rotation in (90, 270):
            w, h = h, w
            logger.info("[PROBE] Rotation=%d detected, swapped to %dx%d", rotation, w, h)

        return w, h
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


def _probe_audio_stream_index(*, input_path: str, ffprobe_bin: str, timeout: int) -> int | None:
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
        return None

    if p.returncode != 0:
        return None

    try:
        data = json.loads(p.stdout)
        audio_streams = [
            stream for stream in data.get("streams", [])
            if stream.get("codec_type") == "audio" and isinstance(stream.get("index"), int)
        ]
        default = next(
            (stream for stream in audio_streams if (stream.get("disposition") or {}).get("default") == 1),
            None,
        )
        selected = default or (audio_streams[0] if audio_streams else None)
        return selected["index"] if selected is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _compute_mid_resolution(input_w: int, input_h: int) -> tuple[int, int]:
    """
    중화질 variant 해상도: 720p 기준 비율 보존 다운스케일.
    원본이 720p 이하면 480p fallback.
    """
    if input_w <= 0 or input_h <= 0:
        return 0, 0

    # 원본이 720p 이하 → 480p로 fallback
    if input_h <= MID_HEIGHT:
        target_h = 480
    else:
        target_h = MID_HEIGHT

    scale = target_h / input_h
    out_w = int(input_w * scale)
    out_h = target_h
    # 짝수 보장
    out_w -= out_w % 2
    out_h -= out_h % 2
    return out_w, out_h


def build_ffmpeg_command(
    *,
    input_path: str,
    hi_w: int,
    hi_h: int,
    mid_w: int,
    mid_h: int,
    two_variants: bool,
    audio_stream_index: int | None,
    ffmpeg_bin: str,
    hls_time: int,
) -> List[str]:
    """
    CRF 기반 2단계 ABR HLS. 원본 비율 보존.
    two_variants=False면 고화질 단일 출력.
    """
    cmd: List[str] = [ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostats", "-y", "-i", input_path]
    with_audio = audio_stream_index is not None

    if two_variants:
        # filter_complex: split → 고화질 스케일 + 중화질 스케일
        hi_scale = f"scale={hi_w}:{hi_h},setsar=1" if hi_w > 0 else "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1"
        mid_scale = f"scale={mid_w}:{mid_h},setsar=1"
        fc = f"[0:v]split=2[vhi][vmid];[vhi]{hi_scale}[vhiout];[vmid]{mid_scale}[vmidout]"
        cmd += ["-filter_complex", fc]

        # v2 = 고화질
        cmd += ["-map", "[vhiout]"]
        if with_audio:
            cmd += ["-map", f"0:{audio_stream_index}"]
        cmd += [
            "-profile:v:0", HI_PROFILE, "-refs:v:0", str(HI_REFERENCE_FRAMES),
            "-crf:v:0", str(HI_CRF),
            "-maxrate:v:0", HI_MAXRATE, "-bufsize:v:0", HI_BUFSIZE,
        ]
        if with_audio:
            cmd += ["-b:a:0", HI_AUDIO_BITRATE]

        # v1 = 중화질
        cmd += ["-map", "[vmidout]"]
        if with_audio:
            cmd += ["-map", f"0:{audio_stream_index}"]
        cmd += [
            "-profile:v:1", MID_PROFILE, "-crf:v:1", str(MID_CRF),
            "-maxrate:v:1", MID_MAXRATE, "-bufsize:v:1", MID_BUFSIZE,
        ]
        if with_audio:
            cmd += ["-b:a:1", MID_AUDIO_BITRATE]

        if with_audio:
            var_map = "v:0,a:0,name:2 v:1,a:1,name:1"
        else:
            var_map = "v:0,name:2 v:1,name:1"
    else:
        # 단일 고화질
        hi_scale = f"scale={hi_w}:{hi_h},setsar=1" if hi_w > 0 else "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1"
        cmd += ["-vf", hi_scale]
        cmd += [
            "-map", "0:v:0",
            "-profile:v", HI_PROFILE, "-refs:v", str(HI_REFERENCE_FRAMES),
            "-crf", str(HI_CRF),
            "-maxrate", HI_MAXRATE, "-bufsize", HI_BUFSIZE,
        ]
        if with_audio:
            cmd += ["-map", f"0:{audio_stream_index}", "-b:a", HI_AUDIO_BITRATE]

        if with_audio:
            var_map = "v:0,a:0,name:1"
        else:
            var_map = "v:0,name:1"

    # Let x264 choose a level for the actual frame size, rate and VBV limits.
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", ENCODING_PRESET,
        "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
    ]
    if with_audio:
        cmd += ["-c:a", "aac", "-ac", "2"]

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
FFMPEG_STALL_TIMEOUT_SECONDS = 900  # 15분 동안 진행 없으면 hang으로 판정하고 kill


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
    hi_w, hi_h = _compute_output_resolution(input_w, input_h)
    mid_w, mid_h = _compute_mid_resolution(input_w, input_h)
    two_variants = 0 < mid_w <= hi_w and mid_h > 0 and hi_h - mid_h >= 100

    logger.info(
        "[TRANSCODER] Resolution: input=%dx%d → hi=%dx%d mid=%dx%d two_variants=%s video_id=%s",
        input_w, input_h, hi_w, hi_h, mid_w, mid_h, two_variants, video_id,
    )

    ensure_dir(output_root)
    ensure_dir(output_root / "v1")
    if two_variants:
        ensure_dir(output_root / "v2")

    audio_stream_index = _probe_audio_stream_index(
        input_path=input_path,
        ffprobe_bin=ffprobe_bin,
        timeout=min(60, effective_timeout),
    )
    cmd = build_ffmpeg_command(
        input_path=input_path,
        hi_w=hi_w,
        hi_h=hi_h,
        mid_w=mid_w,
        mid_h=mid_h,
        two_variants=two_variants,
        audio_stream_index=audio_stream_index,
        ffmpeg_bin=ffmpeg_bin,
        hls_time=hls_time,
    )

    total_sec = float(duration_sec) if duration_sec and duration_sec > 0 else 0.0
    last_pct = -1
    last_progress_time = time.monotonic()
    progress_lock = threading.Lock()
    diagnostics_lock = threading.Lock()
    first_diagnostics: list[str] = []
    recent_diagnostics: deque[str] = deque(maxlen=_FFMPEG_DIAGNOSTIC_LINES - 2)
    input_has_no_video = False
    empty_stream_map = False
    video_map_invalid = False
    output_path = str(output_root.resolve())

    logger.info("[TRANSCODER] Starting ffmpeg for video_id=%s", video_id)
    try:
        with subprocess.Popen(
            cmd,
            cwd=output_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        ) as process:
            assert process.stdout is not None
            assert process.stderr is not None

            def read_stdout() -> None:
                nonlocal last_pct, last_progress_time
                for line in process.stdout:
                    if not total_sec or not (match := _RE_OUT_TIME_MS.search(line)):
                        continue
                    current_sec = int(match.group(1)) / 1_000_000.0
                    with progress_lock:
                        last_progress_time = time.monotonic()
                    pct = min(85, max(50, int(50 + 35 * (current_sec / total_sec))))
                    if pct != last_pct:
                        last_pct = pct
                        if progress_callback is not None:
                            progress_callback(current_sec, total_sec)

            def read_stderr() -> None:
                nonlocal input_has_no_video, empty_stream_map, video_map_invalid
                for line in process.stderr:
                    clean = line.strip()
                    if not clean:
                        continue
                    raw_line = clean
                    for path in (input_path, output_path):
                        clean = clean.replace(path, "<path>")
                    clean = _FFMPEG_URL.sub("<url>", clean)
                    clean = _FFMPEG_CREDENTIAL.sub("<redacted>", clean)
                    clean = _FFMPEG_PATH.sub("<path>", clean)
                    safe_line = "".join(c for c in clean if c.isprintable())[:_FFMPEG_DIAGNOSTIC_CHARS]
                    with diagnostics_lock:
                        input_has_no_video |= bool(_NO_INPUT_VIDEO.fullmatch(raw_line))
                        # Some FFmpeg versions omit the selector in the first error line.
                        empty_stream_map |= raw_line == "Stream map '' matches no streams."
                        video_map_invalid |= raw_line == "Failed to set value '0:v:0' for option 'map': Invalid argument"
                        if len(first_diagnostics) < 2:
                            first_diagnostics.append(safe_line)
                        else:
                            recent_diagnostics.append(safe_line)

            progress_reader = threading.Thread(target=read_stdout, daemon=True)
            stderr_reader = threading.Thread(target=read_stderr, daemon=True)
            progress_reader.start()
            stderr_reader.start()
            registered = bool(job_id and cancel_event is not None)
            if registered:
                from academy.adapters.video.current_transcode import clear_current, set_current

                set_current(process, job_id, cancel_event)
            try:
                deadline = time.monotonic() + effective_timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        process.kill()
                        process.wait()
                        raise TranscodeError(f"ffmpeg timeout video_id={video_id} (extending, cap 24h) exceeded")
                    with progress_lock:
                        stall_sec = time.monotonic() - last_progress_time
                    if total_sec and stall_sec > FFMPEG_STALL_TIMEOUT_SECONDS:
                        process.kill()
                        process.wait()
                        raise TranscodeError(
                            f"ffmpeg stall detected video_id={video_id}: "
                            f"no progress for {int(stall_sec)}s (limit={FFMPEG_STALL_TIMEOUT_SECONDS}s)"
                        )
                    try:
                        process.wait(timeout=min(FFMPEG_CHUNK_SECONDS, int(remaining)))
                        break
                    except subprocess.TimeoutExpired:
                        if not total_sec or stall_sec < FFMPEG_STALL_TIMEOUT_SECONDS:
                            deadline = min(
                                deadline + FFMPEG_EXTEND_SECONDS,
                                time.monotonic() + FFMPEG_MAX_TOTAL_SECONDS,
                            )
                progress_reader.join(timeout=2.0)
                stderr_reader.join(timeout=2.0)
                if cancel_event and cancel_event.is_set():
                    raise CancelledError("Retry requested; ffmpeg SIGTERM sent")
                if process.returncode != 0:
                    with diagnostics_lock:
                        raise FFmpegProcessError(
                            process.returncode,
                            (*first_diagnostics, *recent_diagnostics),
                            invalid_source=process.returncode > 0 and (
                                input_has_no_video or (empty_stream_map and video_map_invalid)
                            ),
                        )
            finally:
                if registered:
                    clear_current()
    except (TranscodeError, CancelledError):
        raise
    except OSError as exc:
        raise TranscodeError(f"ffmpeg process could not run (errno={exc.errno})") from None

    master = output_root / "master.m3u8"
    if not master.exists():
        raise TranscodeError("master.m3u8 not created")

    return master
