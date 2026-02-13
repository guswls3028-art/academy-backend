"""
RedisProgressAdapter - IProgress Port 구현체

Write-Behind: 진행률은 Redis에만 먼저 기록. DB 부하 감소.
매 작업마다 DB를 치지 않고, 최종 완료 시에만 Repository가 DB에 기록.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from src.application.ports.progress import IProgress
from libs.redis.client import get_redis_client

logger = logging.getLogger(__name__)

# 진행 상태 키 TTL (1시간)
PROGRESS_TTL_SECONDS = 3600


class RedisProgressAdapter(IProgress):
    """IProgress 구현 (Redis, Write-Behind)"""

    def __init__(self, ttl_seconds: int = PROGRESS_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds

    def record_progress(
        self,
        job_id: str,
        step: str,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """진행 단계 기록 (Redis에만)"""
        client = get_redis_client()
        if not client:
            return

        key = f"job:{job_id}:progress"
        payload = {"step": step, **(extra or {})}
        try:
            client.setex(
                key,
                self._ttl,
                json.dumps(payload, default=str),
            )
            logger.debug("Progress recorded: job_id=%s step=%s", job_id, step)
        except Exception as e:
            logger.warning("Redis progress record failed: %s", e)

    def get_progress(self, job_id: str) -> Optional[dict[str, Any]]:
        """진행 상태 조회"""
        client = get_redis_client()
        if not client:
            return None

        key = f"job:{job_id}:progress"
        try:
            raw = client.get(key)
            if not raw:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning("Redis progress get failed: %s", e)
            return None
