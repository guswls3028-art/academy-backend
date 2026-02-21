"""
Heartbeat thread에서 cancel_requested 시 ffmpeg subprocess에 SIGTERM 전달용.
transcode_to_hls 시작 시 process 등록, 종료 시 해제.
"""
from __future__ import annotations

import threading
from typing import Optional, Tuple

_process: Optional["subprocess.Popen"] = None
_job_id: Optional[str] = None
_cancel_event: Optional[threading.Event] = None
_lock = threading.Lock()


def set_current(process: "subprocess.Popen", job_id: str, cancel_event: threading.Event) -> None:
    with _lock:
        global _process, _job_id, _cancel_event
        _process = process
        _job_id = job_id
        _cancel_event = cancel_event


def clear_current() -> None:
    with _lock:
        global _process, _job_id, _cancel_event
        _process = None
        _job_id = None
        _cancel_event = None


def get_current() -> Tuple[Optional["subprocess.Popen"], Optional[str], Optional[threading.Event]]:
    with _lock:
        return _process, _job_id, _cancel_event
