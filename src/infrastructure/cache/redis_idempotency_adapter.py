"""
RedisIdempotencyAdapter - IIdempotency Port 구현체

SETNX 기반 멱등성 락.
Worker는 Repository 호출 전 반드시 이 어댑터를 통해 락을 획득해야 함.
"""
from __future__ import annotations

import logging
import threading

from src.application.ports.idempotency import IIdempotency
from libs.redis.client import get_redis_client

logger = logging.getLogger(__name__)

LOG_IDEMPOTENT_SKIP = "IDEMPOTENT_SKIP job_id=%s reason=duplicate"
LOG_LOCK_ACQUIRED = "IDEMPOTENT_LOCK job_id=%s acquired"
LOG_LOCK_RELEASED = "IDEMPOTENT_LOCK job_id=%s released"

# SQS Visibility Timeout (300초)보다 충분히 길게
DEFAULT_LOCK_TTL_SECONDS = 1800  # 30분
# Long Job: 락 획득 후 5분 주기 TTL renew (3시간 인코딩 대비)
RENEW_INTERVAL_SECONDS = 300


class RedisIdempotencyAdapter(IIdempotency):
    """IIdempotency 구현 (Redis SETNX)"""

    def __init__(self, ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._renew_stops: dict[str, threading.Event] = {}
        self._renew_lock = threading.Lock()

    def _renew_loop(self, job_id: str, stop_event: threading.Event) -> None:
        """5분 주기로 TTL renew. 워커 종료 시 stop_event로 중단."""
        key = f"job:{job_id}:lock"
        while not stop_event.wait(timeout=RENEW_INTERVAL_SECONDS):
            client = get_redis_client()
            if not client:
                continue
            try:
                if client.expire(key, self._ttl):
                    logger.debug("IDEMPOTENT_LOCK job_id=%s TTL renewed", job_id)
            except Exception as e:
                logger.warning("Redis lock renew failed job_id=%s: %s", job_id, e)

    def acquire_lock(self, job_id: str) -> bool:
        """
        Job 락 획득 (SETNX 기반)

        Returns:
            True: 락 획득 성공, 작업 진행 가능
            False: 락 획득 실패 (중복 실행)
        """
        client = get_redis_client()
        if not client:
            logger.warning("Redis not available, allowing job (no idempotency)")
            return True

        key = f"job:{job_id}:lock"
        try:
            ok = client.set(key, "1", nx=True, ex=self._ttl)
            if ok:
                logger.debug(LOG_LOCK_ACQUIRED, job_id)
                stop_event = threading.Event()
                with self._renew_lock:
                    self._renew_stops[job_id] = stop_event
                t = threading.Thread(
                    target=self._renew_loop,
                    args=(job_id, stop_event),
                    daemon=True,
                )
                t.start()
                return True
            logger.info(LOG_IDEMPOTENT_SKIP, job_id)
            return False
        except Exception as e:
            logger.warning("Redis lock acquire failed, allowing job: %s", e)
            return True

    def release_lock(self, job_id: str) -> None:
        """작업 완료/실패 시 락 해제 및 renew 중단"""
        with self._renew_lock:
            stop_event = self._renew_stops.pop(job_id, None)
        if stop_event:
            stop_event.set()

        client = get_redis_client()
        if not client:
            return

        key = f"job:{job_id}:lock"
        try:
            client.delete(key)
            logger.debug(LOG_LOCK_RELEASED, job_id)
        except Exception as e:
            logger.warning("Redis lock release failed: %s", e)
