"""
SQS Visibility Extender — 장시간 작업 중 주기적으로 visibility 연장 (AI/Video 공통)
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class SQSVisibilityExtender:
    """
    VisibilityExtenderPort 구현.
    별도 스레드에서 interval_seconds마다 extend_visibility 호출.
    """

    def __init__(self, queue: "AIQueuePort") -> None:
        self._queue = queue
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._receipt_handle: Optional[str] = None
        self._tier: Optional[str] = None
        self._interval_seconds = 60
        self._visibility_timeout_seconds = 3600  # 1시간
        self._lock = threading.Lock()

    def start(
        self,
        receipt_handle: str,
        tier: str,
        interval_seconds: int = 60,
        visibility_timeout_seconds: int = 3600,
    ) -> None:
        """연장 스레드 시작. 이미 동작 중이면 이전 중지 후 새로 시작."""
        self.stop()
        with self._lock:
            self._receipt_handle = receipt_handle
            self._tier = tier
            self._interval_seconds = max(30, interval_seconds)
            self._visibility_timeout_seconds = visibility_timeout_seconds
            self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(
            "Visibility extender started | tier=%s interval=%ds visibility=%ds",
            tier, self._interval_seconds, self._visibility_timeout_seconds,
        )

    def stop(self) -> None:
        """연장 중지."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=self._interval_seconds + 5)
        self._thread = None
        with self._lock:
            self._receipt_handle = None
            self._tier = None
        logger.debug("Visibility extender stopped")

    def _run(self) -> None:
        while not self._stop_event.wait(timeout=self._interval_seconds):
            with self._lock:
                rh, tier = self._receipt_handle, self._tier
                timeout = self._visibility_timeout_seconds
            if not rh or not tier:
                break
            ok = self._queue.extend_visibility(rh, tier, timeout)
            if ok:
                logger.debug("Visibility extended | tier=%s timeout=%ds", tier, timeout)
            else:
                logger.warning("Visibility extend failed | tier=%s", tier)


# 타입 힌트용 (순환 참조 회피)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from academy.application.ports.queues import AIQueuePort
