"""
Redis 기반 멱등성 (중복 실행 방지)

Worker가 job 실행 전에 SETNX 락을 건다.
- 키: job:{job_id}:lock
- TTL: 작업 예상 시간보다 충분히 길게 (10~30분)
- SETNX 실패 시 중복 실행으로 간주 → 즉시 종료
- 작업 완료/실패 시 명시적 DEL
- SQS Visibility Timeout과 TTL 충돌 고려

fail-CLOSED 정책:
- Redis 미사용/장애 시 RedisLockUnavailableError 발생 → 호출부에서 메시지 재시도 결정
- 중복 실행(SETNX 실패)은 False 반환 → 호출부에서 메시지 삭제
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


class RedisLockUnavailableError(Exception):
    """Redis가 사용 불가하여 락을 획득할 수 없음. 메시지를 SQS에 남겨 재시도해야 함."""
    pass


def acquire_job_lock(job_id: str, ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS) -> bool:
    """
    Job 락 획득 (SETNX 기반)

    Returns:
        True: 락 획득 성공, 작업 진행 가능
        False: 락 획득 실패 (이미 다른 워커가 처리 중 = 중복)

    Raises:
        RedisLockUnavailableError: Redis 미사용/장애 → 호출부에서 메시지 재시도 처리
    """
    client = get_redis_client()
    if not client:
        # fail-CLOSED: Redis 미사용 시 예외 발생.
        # 호출부에서 메시지를 SQS에 남겨 Redis 복구 후 재시도.
        logger.warning("Redis not available, REJECTING job for safety (fail-closed) job_id=%s", job_id)
        raise RedisLockUnavailableError(f"Redis not available for job_id={job_id}")

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
        # fail-CLOSED: Redis 장애 시 예외 발생.
        logger.warning("Redis lock acquire failed, REJECTING job for safety: %s", e)
        raise RedisLockUnavailableError(f"Redis lock failed for job_id={job_id}: {e}") from e


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
