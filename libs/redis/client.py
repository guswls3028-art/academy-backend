"""
Redis 클라이언트 - Fallback 지원

Redis 미설정 또는 장애 시 None 반환.
호출부에서 None 체크 후 DB 기반 로직으로 fallback.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_redis_client: Optional[object] = None
_redis_available: Optional[bool] = None


def get_redis_client():
    """
    Redis 클라이언트 반환.
    REDIS_HOST 등이 설정되지 않았거나 연결 실패 시 None.
    """
    global _redis_client, _redis_available

    if _redis_available is False:
        return None

    if _redis_client is not None:
        return _redis_client

    host = os.getenv("REDIS_HOST")
    if not host:
        logger.debug("REDIS_HOST not set, Redis disabled")
        _redis_available = False
        return None

    try:
        import redis
    except ImportError:
        logger.warning("redis package not installed, Redis disabled")
        _redis_available = False
        return None

    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD") or None
    db = int(os.getenv("REDIS_DB", "0"))

    try:
        client = redis.Redis(
            host=host,
            port=port,
            password=password,
            db=db,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        client.ping()
        _redis_client = client
        _redis_available = True
        logger.info("Redis connected: %s:%s db=%s", host, port, db)
        return client
    except Exception as e:
        logger.warning("Redis connection failed (will use DB fallback): %s", e)
        _redis_available = False
        return None


def is_redis_available() -> bool:
    """Redis 사용 가능 여부"""
    client = get_redis_client()
    return client is not None


def reset_redis_state():
    """테스트용: Redis 상태 리셋"""
    global _redis_client, _redis_available
    _redis_client = None
    _redis_available = None
