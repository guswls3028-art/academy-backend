# PATH: apps/domains/matchup/cache.py
# 매치업 검색 결과 캐싱 — find_similar_problems 부하 완화

from __future__ import annotations

import json
import logging
from typing import List, Optional, Tuple

from libs.redis import get_redis_client

logger = logging.getLogger(__name__)

# v1: 알고리즘 변경 시 prefix bump 으로 일괄 무효화.
_KEY_PREFIX = "matchup:similar:v1"
_TTL_SECONDS = 3600  # 1시간


def _key(tenant_id: int, problem_id: int, top_k: int, author_id: Optional[int]) -> str:
    return f"{_KEY_PREFIX}:{tenant_id}:{problem_id}:{top_k}:{author_id or 0}"


def get_cached_similar(
    tenant_id: int, problem_id: int, top_k: int, author_id: Optional[int],
) -> Optional[List[Tuple[int, float]]]:
    """캐시 hit: [(problem_id, score), ...]. miss/redis 장애 시 None.

    fail-OPEN: redis 미사용/오류는 None → 호출부가 DB 풀 fetch fallback.
    """
    client = get_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_key(tenant_id, problem_id, top_k, author_id))
    except Exception as e:
        logger.warning("matchup cache get failed (fallback to DB): %s", e)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return [(int(pid), float(score)) for pid, score in data]
    except (ValueError, TypeError) as e:
        logger.warning("matchup cache decode failed key=%s: %s",
                       _key(tenant_id, problem_id, top_k, author_id), e)
        return None


def set_cached_similar(
    tenant_id: int, problem_id: int, top_k: int, author_id: Optional[int],
    results: List[Tuple[int, float]],
) -> None:
    """[(problem_id, score), ...] 캐싱. redis 미사용/오류 시 silent."""
    client = get_redis_client()
    if client is None:
        return
    try:
        payload = json.dumps([[int(pid), float(score)] for pid, score in results])
        client.set(
            _key(tenant_id, problem_id, top_k, author_id),
            payload,
            ex=_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning("matchup cache set failed: %s", e)
