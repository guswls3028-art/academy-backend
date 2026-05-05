"""매치업 검색 결과 redis 캐싱 + image lazy fetch 동작 검증.

cache.py 자체는 redis client mock 으로 단위 테스트 가능 (DB 무관).
services.py find_similar_problems 는 inspect 기반 정합성만 검증
(SQLite meta__contains 미지원으로 통합 테스트 어려움).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest


def test_cache_module_get_set_roundtrip():
    """redis client 정상 동작 시 set → get 동일 [(id, score)] 반환."""
    from apps.domains.matchup import cache as mc

    fake_redis = MagicMock()
    storage: dict = {}

    def _set(key, value, ex=None):
        storage[key] = value
        return True

    def _get(key):
        return storage.get(key)

    fake_redis.set.side_effect = _set
    fake_redis.get.side_effect = _get

    with patch("apps.domains.matchup.cache.get_redis_client", return_value=fake_redis):
        mc.set_cached_similar(
            tenant_id=1, problem_id=42, top_k=10, author_id=7,
            results=[(101, 0.95), (102, 0.88), (103, 0.81)],
        )
        result = mc.get_cached_similar(
            tenant_id=1, problem_id=42, top_k=10, author_id=7,
        )

    assert result == [(101, 0.95), (102, 0.88), (103, 0.81)]
    # TTL 1h 적용 확인
    fake_redis.set.assert_called_once()
    _, kwargs = fake_redis.set.call_args
    assert kwargs["ex"] == 3600


def test_cache_key_includes_all_params():
    """cache key 가 tenant/problem/top_k/author 모두 반영 — 격리 보장."""
    from apps.domains.matchup import cache as mc

    fake_redis = MagicMock()
    fake_redis.get.return_value = None

    with patch("apps.domains.matchup.cache.get_redis_client", return_value=fake_redis):
        mc.get_cached_similar(tenant_id=1, problem_id=42, top_k=10, author_id=7)
        mc.get_cached_similar(tenant_id=2, problem_id=42, top_k=10, author_id=7)
        mc.get_cached_similar(tenant_id=1, problem_id=43, top_k=10, author_id=7)
        mc.get_cached_similar(tenant_id=1, problem_id=42, top_k=20, author_id=7)
        mc.get_cached_similar(tenant_id=1, problem_id=42, top_k=10, author_id=8)
        mc.get_cached_similar(tenant_id=1, problem_id=42, top_k=10, author_id=None)

    keys = [c.args[0] for c in fake_redis.get.call_args_list]
    assert len(set(keys)) == 6, "각 파라미터 변화마다 별 키 생성"
    # author_id=None → 0 매핑 (key segment 안정)
    assert keys[-1].endswith(":0")


def test_cache_fail_open_when_redis_unavailable():
    """REDIS_HOST 미설정/장애 시 get/set 모두 silent (None / no-op)."""
    from apps.domains.matchup import cache as mc

    with patch("apps.domains.matchup.cache.get_redis_client", return_value=None):
        result = mc.get_cached_similar(
            tenant_id=1, problem_id=42, top_k=10, author_id=None,
        )
        # set 도 예외 없이 silent return
        mc.set_cached_similar(
            tenant_id=1, problem_id=42, top_k=10, author_id=None,
            results=[(101, 0.9)],
        )

    assert result is None


def test_cache_get_handles_redis_exception():
    """redis client 예외 (timeout 등) 시 None 반환 — fail-OPEN."""
    from apps.domains.matchup import cache as mc

    fake_redis = MagicMock()
    fake_redis.get.side_effect = ConnectionError("redis down")

    with patch("apps.domains.matchup.cache.get_redis_client", return_value=fake_redis):
        result = mc.get_cached_similar(
            tenant_id=1, problem_id=42, top_k=10, author_id=None,
        )

    assert result is None


def test_cache_get_handles_corrupted_payload():
    """저장된 JSON 깨짐 시 None — DB fallback 동작."""
    from apps.domains.matchup import cache as mc

    fake_redis = MagicMock()
    fake_redis.get.return_value = "not a valid json"

    with patch("apps.domains.matchup.cache.get_redis_client", return_value=fake_redis):
        result = mc.get_cached_similar(
            tenant_id=1, problem_id=42, top_k=10, author_id=None,
        )

    assert result is None


def test_find_similar_uses_cache_module():
    """services.py find_similar_problems 가 cache 모듈 import + 호출."""
    from apps.domains.matchup import services

    src = inspect.getsource(services.find_similar_problems)
    assert "get_cached_similar" in src, "캐시 read 누락"
    assert "set_cached_similar" in src, "캐시 write 누락"
    assert "in_bulk" in src, "캐시 hit 시 PK 단일 쿼리 누락"


def test_find_similar_image_embedding_lazy_fetch():
    """services.py 1차 fetch 에서 image_embedding 컬럼 제외 (lazy fetch)."""
    from apps.domains.matchup import services

    src = inspect.getsource(services.find_similar_problems)
    # 1차 .only(...) 에 image_embedding 없어야 함
    assert '"id", "document_id", "embedding",\n' in src or \
           '"id", "document_id", "embedding"' in src
    # image_embedding 별도 fetch (PK in_filter)
    assert "image_embedding" in src
    assert "filter(id__in=" in src or "filter(id__in =" in src
