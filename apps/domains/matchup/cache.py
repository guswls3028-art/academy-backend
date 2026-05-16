# PATH: apps/domains/matchup/cache.py
# 매치업 검색 결과 캐싱 — find_similar_problems 부하 완화

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional, Sequence, Tuple

from libs.redis import get_redis_client

logger = logging.getLogger(__name__)

# v1: 알고리즘 변경 시 prefix bump 으로 일괄 무효화.
_KEY_PREFIX = "matchup:similar:v1"
# TTL 5분 (2026-05-05 학원장 결함 fix): 1시간이면 사용자/학원장이 manual cut 후
# 새 problem이 풀에 들어와도 기존 source의 추천 캐시가 1시간 stale → "내가 자른
# 문제들이 안 올라오네" 결함. invalidate 함수 미구현 상태에서 안전장치로 단축.
# 5분 = redis hit률 유지 + 신규 manual 빠른 반영 균형점.
_TTL_SECONDS = 300  # 5분

SimilarityBreakdown = dict[str, Any]
SimilarCacheEntry = Tuple[int, float] | Tuple[int, float, SimilarityBreakdown]


def _key(tenant_id: int, problem_id: int, top_k: int, author_id: Optional[int]) -> str:
    return f"{_KEY_PREFIX}:{tenant_id}:{problem_id}:{top_k}:{author_id or 0}"


def get_cached_similar(
    tenant_id: int, problem_id: int, top_k: int, author_id: Optional[int],
) -> Optional[List[SimilarCacheEntry]]:
    """캐시 hit: [(problem_id, score[, breakdown]), ...]. miss/redis 장애 시 None.

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
        return [_decode_entry(item) for item in data]
    except (ValueError, TypeError) as e:
        logger.warning("matchup cache decode failed key=%s: %s",
                       _key(tenant_id, problem_id, top_k, author_id), e)
        return None


def set_cached_similar(
    tenant_id: int, problem_id: int, top_k: int, author_id: Optional[int],
    results: List[SimilarCacheEntry],
) -> None:
    """[(problem_id, score[, breakdown]), ...] 캐싱. redis 미사용/오류 시 silent."""
    client = get_redis_client()
    if client is None:
        return
    try:
        payload = json.dumps([_encode_entry(entry) for entry in results])
        client.set(
            _key(tenant_id, problem_id, top_k, author_id),
            payload,
            ex=_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning("matchup cache set failed: %s", e)


def _encode_entry(entry: Sequence[Any]) -> list[Any]:
    if len(entry) < 2:
        raise ValueError("cache entry must contain problem_id and score")
    encoded: list[Any] = [int(entry[0]), float(entry[1])]
    if len(entry) >= 3:
        breakdown = entry[2]
        if breakdown is not None:
            encoded.append(breakdown if isinstance(breakdown, dict) else {})
    return encoded


def _decode_entry(item: Sequence[Any]) -> SimilarCacheEntry:
    if len(item) < 2:
        raise ValueError("cache entry must contain problem_id and score")
    base = (int(item[0]), float(item[1]))
    if len(item) >= 3:
        breakdown = item[2] if isinstance(item[2], dict) else {}
        return (base[0], base[1], breakdown)
    return base


def invalidate_tenant_similar_cache(tenant_id: int) -> int:
    """tenant 전체 매치업 검색 캐시 무효화.

    호출 시점: manual_crop / paste_problem / merge_problems / retry_document /
    bulk_delete 등 problem 풀 변경되는 mutation 직후. 학원장이 자른 신규 manual이
    즉시 검색 결과에 반영되도록.

    redis SCAN으로 prefix 매칭 key 일괄 DELETE. fail-OPEN.
    Returns: 삭제된 key 수 (best-effort).
    """
    client = get_redis_client()
    if client is None:
        return 0
    pattern = f"{_KEY_PREFIX}:{tenant_id}:*"
    deleted = 0
    try:
        for k in client.scan_iter(match=pattern, count=200):
            try:
                client.delete(k)
                deleted += 1
            except Exception:
                pass
    except Exception as e:
        logger.warning("matchup cache invalidate failed (tenant=%s): %s", tenant_id, e)
    return deleted
