# PATH: apps/worker/video_worker/heartbeat.py
#
# PURPOSE:
# - long-running 작업 동안 backend에 주기적으로 heartbeat 전송
# - backend의 reclaim 오판 방지
#
# CONTRACT:
# - POST /api/v1/internal/video-worker/{video_id}/heartbeat/
# - 실패해도 worker 메인 작업은 중단하지 않음
#
# DESIGN:
# - exponential backoff
# - stop() 호출 시 즉시 종료
# - thread-safe

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from apps.worker.video_worker.http_client import VideoAPIClient

logger = logging.getLogger("video_worker.heartbeat")


class HeartbeatThread:
    def __init__(
        self,
        *,
        client: VideoAPIClient,
        video_id: int,
        interval: int,
        backoff_base: int,
        backoff_cap: int,
    ):
        self._client = client
        self._video_id = video_id
        self._interval = max(1, int(interval))
        self._backoff_base = max(1, int(backoff_base))
        self._backoff_cap = max(self._backoff_base, int(backoff_cap))

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return

        self._thread = threading.Thread(
            target=self._run,
            name=f"heartbeat-video-{self._video_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        backoff = self._backoff_base

        # 최초 1회는 바로 보내지 않고 interval 이후 전송
        next_sleep = self._interval

        while not self._stop_event.wait(next_sleep):
            try:
                self._client.send_heartbeat(self._video_id)
                # 성공 시 backoff 리셋
                backoff = self._backoff_base
                next_sleep = self._interval
            except Exception as e:
                # heartbeat 실패는 치명적이지 않다
                logger.warning(
                    "heartbeat failed video_id=%s err=%s",
                    self._video_id,
                    e,
                )
                # backoff 증가
                next_sleep = min(backoff, self._backoff_cap)
                backoff = min(backoff * 2, self._backoff_cap)
