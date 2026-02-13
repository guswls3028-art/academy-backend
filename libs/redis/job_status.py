"""
Redis 기반 실시간 Job 상태 (SSOT)

Worker 진행률은 DB가 아닌 Redis에 기록.
- 키: job:{job_id}:status
- Hash/JSON: status, progress, current_step, updated_at
- TTL: 1시간
- 작업 완료 시 최종 상태만 DB 반영
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from libs.redis.client import get_redis_client

logger = logging.getLogger(__name__)

STATUS_TTL_SECONDS = 3600  # 1시간


def set_job_status(
    job_id: str,
    *,
    status: str = "processing",
    progress: int = 0,
    current_step: str = "",
    extra: Optional[dict] = None,
) -> bool:
    """Job 상태 기록 (Redis)"""
    client = get_redis_client()
    if not client:
        return False

    key = f"job:{job_id}:status"
    data = {
        "status": status,
        "progress": min(100, max(0, progress)),
        "current_step": current_step or "",
        "updated_at": time.time(),
    }
    if extra:
        data.update(extra)

    try:
        client.set(key, json.dumps(data), ex=STATUS_TTL_SECONDS)
        return True
    except Exception as e:
        logger.warning("Redis job status set failed: %s", e)
        return False


def get_job_status(job_id: str) -> Optional[dict]:
    """Job 상태 조회 (Redis)"""
    client = get_redis_client()
    if not client:
        return None

    key = f"job:{job_id}:status"
    try:
        raw = client.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.warning("Redis job status get failed: %s", e)
        return None


def publish_job_status_event(job_id: str, payload: dict) -> bool:
    """
    Job 상태 변경 이벤트 발행 (Pub/Sub)
    SSE/WebSocket 구독자가 수신할 수 있음
    """
    client = get_redis_client()
    if not client:
        return False

    channel = f"job:{job_id}:events"
    try:
        client.publish(channel, json.dumps(payload))
        return True
    except Exception as e:
        logger.warning("Redis job status publish failed: %s", e)
        return False


def delete_job_status(job_id: str) -> None:
    """작업 완료 후 상태 키 삭제 (선택적)"""
    client = get_redis_client()
    if not client:
        return

    key = f"job:{job_id}:status"
    try:
        client.delete(key)
    except Exception as e:
        logger.warning("Redis job status delete failed: %s", e)
