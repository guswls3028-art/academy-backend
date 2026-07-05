"""Compatibility facade for video Redis cache helpers.

Redis implementation lives in academy.adapters.cache.redis_video_status_cache.
"""

from __future__ import annotations

from academy.adapters.cache.redis_video_status_cache import (
    VIDEO_ASG_INTERRUPT_KEY,
    VIDEO_ASG_INTERRUPT_TTL_SECONDS,
    VIDEO_BACKLOG_KEY_PATTERN,
    VIDEO_HEARTBEAT_TTL_SECONDS,
    cache_video_status,
    delete_video_heartbeat,
    delete_video_progress_key,
    get_video_progress_payload,
    get_video_status_from_redis,
    has_video_heartbeat,
    is_asg_interrupt,
    is_cancel_requested,
    redis_decr_video_backlog,
    redis_get_video_backlog_total,
    redis_incr_video_backlog,
    refresh_video_progress_ttl,
    set_asg_interrupt,
    set_cancel_requested,
    set_video_heartbeat,
)

__all__ = [
    "VIDEO_ASG_INTERRUPT_KEY",
    "VIDEO_ASG_INTERRUPT_TTL_SECONDS",
    "VIDEO_BACKLOG_KEY_PATTERN",
    "VIDEO_HEARTBEAT_TTL_SECONDS",
    "cache_video_status",
    "delete_video_heartbeat",
    "delete_video_progress_key",
    "get_video_progress_payload",
    "get_video_status_from_redis",
    "has_video_heartbeat",
    "is_asg_interrupt",
    "is_cancel_requested",
    "redis_decr_video_backlog",
    "redis_get_video_backlog_total",
    "redis_incr_video_backlog",
    "refresh_video_progress_ttl",
    "set_asg_interrupt",
    "set_cancel_requested",
    "set_video_heartbeat",
]
