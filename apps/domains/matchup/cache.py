"""Compatibility facade for matchup similarity cache helpers.

Redis implementation lives in academy.adapters.cache.redis_matchup_similarity_cache.
"""

from __future__ import annotations

from academy.adapters.cache.redis_matchup_similarity_cache import (
    SimilarCacheEntry,
    SimilarityBreakdown,
    get_cached_similar,
    invalidate_tenant_similar_cache,
    set_cached_similar,
)

__all__ = [
    "SimilarCacheEntry",
    "SimilarityBreakdown",
    "get_cached_similar",
    "invalidate_tenant_similar_cache",
    "set_cached_similar",
]
