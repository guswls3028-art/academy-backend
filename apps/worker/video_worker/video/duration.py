# PATH: apps/worker/video_worker/video/duration.py
#
# PURPOSE:
# - 로컬 영상 파일에서 ffprobe로 duration(초) 추출
# - 실패해도 worker 전체 작업을 fail 시키지 않음 (best-effort)

from __future__ import annotations

import subprocess
from typing import Optional


class DurationProbeError(RuntimeError):
    pass


def probe_duration_seconds(
    *,
    input_path: str,
    ffprobe_bin: str,
    timeout: int,
) -> Optional[int]:
    if not input_path:
        return None

    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
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
    except Exception:
        return None

    if p.returncode != 0:
        return None

    raw = (p.stdout or "").strip()
    if not raw:
        return None

    try:
        sec = float(raw)
        if sec < 0:
            return None
        return int(sec)
    except Exception:
        return None
