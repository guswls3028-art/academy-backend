from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("video_worker.http")


class VideoAPIClient:
    def __init__(
        self,
        *,
        base_url: str,
        worker_token: str,
        worker_id: str,
        timeout_seconds: int,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._headers = {
            "X-Worker-Token": worker_token,
            "X-Worker-Id": worker_id,
            "Content-Type": "application/json",
        }

    # --------------------------------------------------
    # Job control
    # --------------------------------------------------

    def fetch_next_job(self) -> Optional[Dict[str, Any]]:
        url = f"{self._base_url}/internal/video-worker/next/"
        resp = requests.get(
            url,
            headers=self._headers,
            timeout=self._timeout,
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        return resp.json()

    def notify_complete(self, video_id: int, payload: Dict[str, Any]) -> None:
        url = f"{self._base_url}/internal/video-worker/{video_id}/complete/"
        resp = requests.post(
            url,
            json=payload,
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()

    def notify_fail(self, video_id: int, reason: str) -> None:
        url = f"{self._base_url}/internal/video-worker/{video_id}/fail/"
        resp = requests.post(
            url,
            json={"reason": reason},
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()

    # --------------------------------------------------
    # Heartbeat
    # --------------------------------------------------

    def send_heartbeat(self, video_id: int) -> None:
        url = f"{self._base_url}/internal/video-worker/{video_id}/heartbeat/"
        resp = requests.post(
            url,
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()

    # --------------------------------------------------
    # Lifecycle
    # --------------------------------------------------

    def close(self) -> None:
        """
        requests.Session을 쓰지 않으므로 noop.
        main.py의 client.close() 계약을 맞추기 위한 메서드.
        """
        return
