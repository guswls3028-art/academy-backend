"""Redis-backed playback session buffer adapter.

Domain services use these fail-open helpers instead of importing libs.redis
directly. Redis outages fall back to the DB path in the caller.
"""

from __future__ import annotations

from typing import Any


def is_redis_available() -> bool:
    try:
        from libs.redis import is_redis_available as _is_redis_available

        return bool(_is_redis_available())
    except Exception:
        return False


def init_session_redis(*args: Any, **kwargs: Any) -> bool:
    try:
        from libs.redis.watch_buffer import init_session_redis as _init_session_redis

        return bool(_init_session_redis(*args, **kwargs))
    except Exception:
        return False


def buffer_heartbeat_session_ttl(*args: Any, **kwargs: Any) -> bool:
    try:
        from libs.redis.watch_buffer import buffer_heartbeat_session_ttl as _buffer_heartbeat_session_ttl

        return bool(_buffer_heartbeat_session_ttl(*args, **kwargs))
    except Exception:
        return False


def buffer_session_event(*args: Any, **kwargs: Any) -> tuple[bool, dict[str, int]]:
    try:
        from libs.redis.watch_buffer import buffer_session_event as _buffer_session_event

        ok, stats = _buffer_session_event(*args, **kwargs)
        return bool(ok), stats
    except Exception:
        return False, {"total": 0, "violated": 0}


def get_session_violation_stats_redis(*args: Any, **kwargs: Any) -> dict[str, int] | None:
    try:
        from libs.redis.watch_buffer import get_session_violation_stats_redis as _get_session_violation_stats_redis

        return _get_session_violation_stats_redis(*args, **kwargs)
    except Exception:
        return None


def flush_session_stats(*args: Any, **kwargs: Any) -> None:
    try:
        from libs.redis.watch_buffer import flush_session_stats as _flush_session_stats

        _flush_session_stats(*args, **kwargs)
    except Exception:
        pass


def flush_session_buffer(*args: Any, **kwargs: Any) -> bool:
    try:
        from libs.redis.watch_buffer import flush_session_buffer as _flush_session_buffer

        return bool(_flush_session_buffer(*args, **kwargs))
    except Exception:
        return False


def has_session_meta(session_id: str) -> bool:
    try:
        from libs.redis.client import get_redis_client

        client = get_redis_client()
        return bool(client and client.exists(f"session:{session_id}:meta"))
    except Exception:
        return False
