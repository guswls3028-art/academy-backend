# PATH: apps/worker/video_worker/http_client.py
from __future__ import annotations

from typing import Any, Dict, Optional
import requests


class VideoAPIClient:
    """
    Internal API client (SSOT)
    - Token: INTERNAL_WORKER_TOKEN
    - Prefix: /api/v1/internal/video-worker/*
    """

    def __init__(
        self,
        *,
        base_url: str,
        internal_worker_token: str,
        worker_id: str,
        timeout_seconds: float,
    ):
        self._base = base_url.rstrip("/") + "/api/v1/internal/video-worker"
        self._timeout = timeout_seconds
        self._headers = {
            "X-Worker-Token": internal_worker_token,
            "X-Worker-Id": worker_id,
            "Content-Type": "application/json",
        }

    def fetch_next_job(self) -> Optional[Dict[str, Any]]:
        r = requests.get(
            f"{self._base}/next/",
            headers=self._headers,
            timeout=self._timeout,
        )
        if r.status_code == 204:
            return None
        r.raise_for_status()
        data = r.json()
        return data.get("job") if isinstance(data, dict) else None

    def notify_complete(self, video_id: int, payload: Dict[str, Any]) -> None:
        r = requests.post(
            f"{self._base}/{video_id}/complete/",
            json=payload,
            headers=self._headers,
            timeout=self._timeout,
        )
        r.raise_for_status()

    def notify_fail(self, video_id: int, reason: str) -> None:
        r = requests.post(
            f"{self._base}/{video_id}/fail/",
            json={"reason": reason},
            headers=self._headers,
            timeout=self._timeout,
        )
        r.raise_for_status()

    def send_heartbeat(self, video_id: int) -> None:
        r = requests.post(
            f"{self._base}/{video_id}/heartbeat/",
            headers=self._headers,
            timeout=self._timeout,
        )
        r.raise_for_status()

    def close(self) -> None:
        return
