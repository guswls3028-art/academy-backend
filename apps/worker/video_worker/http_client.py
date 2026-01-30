# PATH: apps/worker/video_worker/http_client.py
#
# PURPOSE:
# - backend internal video-worker API 전용 HTTP client
# - worker는 서버 내부 구현/DB를 알지 않는다
#
# ENDPOINTS:
# - GET  /api/v1/internal/video-worker/next/
# - POST /api/v1/internal/video-worker/{id}/complete/
# - POST /api/v1/internal/video-worker/{id}/fail/
# - POST /api/v1/internal/video-worker/{id}/heartbeat/
#
# DESIGN:
# - timeout 명시
# - retry는 heartbeat thread가 담당
# - worker_id(lease owner)를 모든 요청에 포함

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from apps.worker.video_worker.config import Config

logger = logging.getLogger("video_worker.http")


class VideoAPIClient:
    """
    ✅ FIXED:
    - 기존 main.py가 VideoAPIClient(cfg)로 호출하던 계약을 보존하면서 동작하도록 지원
    - 모든 요청에 X-Worker-Id 포함 → backend lease owner 검증 충족
    - close() 제공 → main.py finally에서 AttributeError 방지
    """

    def __init__(
        self,
        cfg: Optional[Config] = None,
        *,
        base_url: Optional[str] = None,
        worker_token: Optional[str] = None,
        worker_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ):
        # --------------------------------------------
        # Backward compatible constructor
        #   - VideoAPIClient(cfg)  ✅
        #   - VideoAPIClient(base_url=..., worker_token=..., ...) ✅
        # --------------------------------------------
        if cfg is not None:
            base_url = cfg.API_BASE_URL
            worker_token = cfg.WORKER_TOKEN
            worker_id = cfg.WORKER_ID
            timeout_seconds = cfg.HTTP_TIMEOUT_SECONDS

        if not base_url:
            raise ValueError("base_url is required")
        if not worker_token:
            raise ValueError("worker_token is required")

        self._base_url = str(base_url).rstrip("/")
        self._timeout = float(timeout_seconds or 10.0)

        self._headers = {
            "X-Worker-Token": str(worker_token),
            "Content-Type": "application/json",
        }
        # ✅ lease owner를 backend에 전달 (문제 4 해결의 필수 조건)
        if worker_id:
            self._headers["X-Worker-Id"] = str(worker_id)

        # requests.Session을 쓰면 keep-alive로 효율 + close 가능
        self._session = requests.Session()

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    # --------------------------------------------------
    # Job control
    # --------------------------------------------------

    def fetch_next_job(self) -> Optional[Dict[str, Any]]:
        url = f"{self._base_url}/internal/video-worker/next/"
        resp = self._session.get(
            url,
            headers=self._headers,
            timeout=self._timeout,
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        # backend는 {"job": {...}}를 주므로 호출자 편의 제공
        if isinstance(data, dict) and "job" in data and isinstance(data["job"], dict):
            return data["job"]
        return data

    def notify_complete(self, video_id: int, payload: Dict[str, Any]) -> None:
        url = f"{self._base_url}/internal/video-worker/{int(video_id)}/complete/"
        resp = self._session.post(
            url,
            json=payload,
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()

    def notify_fail(self, video_id: int, reason: str) -> None:
        url = f"{self._base_url}/internal/video-worker/{int(video_id)}/fail/"
        resp = self._session.post(
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
        """
        Heartbeat는 best-effort.
        실패 시 예외를 던지지만,
        caller(HeartbeatThread)가 삼켜서 재시도/backoff 한다.
        """
        url = f"{self._base_url}/internal/video-worker/{int(video_id)}/heartbeat/"
        resp = self._session.post(
            url,
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
