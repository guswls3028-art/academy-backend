"""
Redis 기반 멱등성 (중복 실행 방지)

Worker가 job 실행 전에 SETNX 락을 건다.
- 키: job:{job_id}:lock
- TTL: 작업 예상 시간보다 충분히 길게 (10~30분)
- SETNX 실패 시 중복 실행으로 간주 → 즉시 종료
- 작업 완료/실패 시 명시적 DEL
- SQS Visibility Timeout과 TTL 충돌 고려
"""

from __future__ import annotations

import logging
from typing import Optional

from libs.redis.client import get_redis_client

logger = logging.getLogger(__name__)

# 멱등성 로그 포맷 (표준화)
LOG_IDEMPOTENT_SKIP = "IDEMPOTENT_SKIP job_id=%s reason=duplicate"
LOG_LOCK_ACQUIRED = "IDEMPOTENT_LOCK job_id=%s acquired"
LOG_LOCK_RELEASED = "IDEMPOTENT_LOCK job_id=%s released"

# SQS Visibility Timeout (300초)보다 충분히 길게
DEFAULT_LOCK_TTL_SECONDS = 1800  # 30분


def acquire_job_lock(job_id: str, ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS) -> bool:
    """
    Job 락 획득 (SETNX 기반)

    Returns:
        True: 락 획득 성공, 작업 진행 가능
        False: 락 획득 실패 (중복 실행) 또는 Redis 미사용 → 호출부에서 DB fallback 판단
    """
    client = get_redis_client()
    if not client:
        # Redis 미사용: 기존 DB/Worker 로직 그대로 진행 (fallback)
        return True

    key = f"job:{job_id}:lock"
    try:
        # SET key value NX EX ttl
        ok = client.set(key, "1", nx=True, ex=ttl_seconds)
        if ok:
            logger.debug(LOG_LOCK_ACQUIRED, job_id)
            return True
        logger.info(LOG_IDEMPOTENT_SKIP, job_id)
        return False
    except Exception as e:
        logger.warning("Redis lock acquire failed, allowing job: %s", e)
        return True  # Redis 장애 시 기존 로직 진행


def release_job_lock(job_id: str) -> None:
    """작업 완료/실패 시 락 해제"""
    client = get_redis_client()
    if not client:
        return

    key = f"job:{job_id}:lock"
    try:
        client.delete(key)
        logger.debug(LOG_LOCK_RELEASED, job_id)
    except Exception as e:
        logger.warning("Redis lock release failed: %s", e)
        # TTL 만료 시 자동 해제되므로 치명적이지 않음
