"""Compatibility facade for AI job Redis cache helpers.

Redis implementation lives in academy.adapters.cache.redis_ai_job_status_cache.
"""

from __future__ import annotations

from academy.adapters.cache.redis_ai_job_status_cache import (
    cache_job_status,
    get_job_status_from_redis,
    refresh_job_progress_ttl,
)

__all__ = [
    "cache_job_status",
    "get_job_status_from_redis",
    "refresh_job_progress_ttl",
]
