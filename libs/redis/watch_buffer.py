"""
영상 시청 Heartbeat 버퍼링 및 정책 판독

- 5초 주기 heartbeat는 DB에 직접 쓰지 않고 Redis에 버퍼링
- key: session:{session_id}:watch (Sorted Set, timestamp 기반)
- 정책 위반(배속/스킵 등) 시 Redis에서 즉시 판독
- Lua Script 기반 원자적 검사
- 위반 시 user:{user_id}:blocked = 1 (TTL 포함)
- DB에는 최종 시청 결과만 Write-Behind
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional, Tuple

from libs.redis.client import get_redis_client

logger = logging.getLogger(__name__)

# Heartbeat 버퍼 TTL (세션 종료 후 유지 시간)
WATCH_BUFFER_TTL = 7200  # 2시간
BLOCKED_TTL = 86400  # 24시간
SESSION_META_TTL = 7200  # 2시간

# Lua: heartbeat 기록 + violated 체크 (원자적)
LUA_HEARTBEAT_VIOLATION = """
local watch_key = KEYS[1]
local blocked_key = KEYS[2]
local meta_key = KEYS[3]
local score = tonumber(ARGV[1])
local member = ARGV[2]
local violated = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local block_ttl = tonumber(ARGV[5])

redis.call('ZADD', watch_key, score, member)
redis.call('EXPIRE', watch_key, ttl)

if violated == 1 then
    redis.call('SET', blocked_key, '1', 'EX', block_ttl)
    return 1
end
return 0
"""


def buffer_heartbeat(
    session_id: str,
    user_id: int,
    *,
    timestamp: Optional[float] = None,
    violated: bool = False,
    position_seconds: float = 0,
) -> bool:
    """
    Heartbeat를 Redis Sorted Set에 버퍼링.
    위반 시 blocked 플래그 설정.
    """
    client = get_redis_client()
    if not client:
        return False

    ts = timestamp or time.time()
    member = json.dumps({"ts": ts, "pos": position_seconds, "v": 1 if violated else 0})
    watch_key = f"session:{session_id}:watch"
    blocked_key = f"user:{user_id}:blocked"
    meta_key = f"session:{session_id}:meta"

    try:
        client.zadd(watch_key, {member: ts})
        client.expire(watch_key, WATCH_BUFFER_TTL)

        if violated:
            client.set(blocked_key, "1", ex=BLOCKED_TTL)

        return True
    except Exception as e:
        logger.warning("Redis heartbeat buffer failed: %s", e)
        return False


def init_session_redis(session_id: str, ttl_seconds: int) -> bool:
    """세션 시작 시 Redis 키 초기화 (heartbeat 대기 전 활성 상태)"""
    client = get_redis_client()
    if not client:
        return False

    meta_key = f"session:{session_id}:meta"
    try:
        client.set(meta_key, str(time.time()), ex=ttl_seconds)
        return True
    except Exception as e:
        logger.warning("Redis session init failed: %s", e)
        return False


def buffer_heartbeat_session_ttl(session_id: str, ttl_seconds: int) -> bool:
    """
    Heartbeat TTL 연장 (last_seen 갱신)
    DB 대신 Redis에서 세션 만료 관리
    """
    client = get_redis_client()
    if not client:
        return False

    meta_key = f"session:{session_id}:meta"
    watch_key = f"session:{session_id}:watch"
    try:
        client.set(meta_key, str(time.time()), ex=ttl_seconds)
        client.expire(watch_key, WATCH_BUFFER_TTL)
        return True
    except Exception as e:
        logger.warning("Redis session TTL extend failed: %s", e)
        return False


def is_user_blocked(user_id: int) -> bool:
    """Redis에서 차단 플래그 확인"""
    client = get_redis_client()
    if not client:
        return False

    key = f"user:{user_id}:blocked"
    try:
        return client.exists(key) > 0
    except Exception as e:
        logger.warning("Redis blocked check failed: %s", e)
        return False


def get_session_watch_buffer(session_id: str) -> list:
    """세션별 버퍼된 heartbeat 목록 (Write-Behind flush용)"""
    client = get_redis_client()
    if not client:
        return []

    key = f"session:{session_id}:watch"
    try:
        items = client.zrange(key, 0, -1, withscores=True)
        return [{"member": m, "score": s} for m, s in items]
    except Exception as e:
        logger.warning("Redis watch buffer get failed: %s", e)
        return []


def flush_session_buffer(session_id: str) -> bool:
    """세션 종료 시 버퍼 삭제 (Write-Behind 완료 후)"""
    client = get_redis_client()
    if not client:
        return False

    for key in (f"session:{session_id}:watch", f"session:{session_id}:meta"):
        try:
            client.delete(key)
        except Exception as e:
            logger.warning("Redis flush failed %s: %s", key, e)
    return True


def buffer_session_event(
    session_id: str,
    user_id: int,
    *,
    violated: bool = False,
    reason: str = "",
) -> Tuple[bool, dict]:
    """
    record_session_event 대체: Redis에 이벤트 누적
    Returns: (성공여부, {total, violated})
    """
    client = get_redis_client()
    if not client:
        return False, {"total": 0, "violated": 0}

    total_key = f"session:{session_id}:stats:total"
    violated_key = f"session:{session_id}:stats:violated"

    try:
        client.incr(total_key)
        if violated:
            client.incr(violated_key)
        client.expire(total_key, SESSION_META_TTL)
        client.expire(violated_key, SESSION_META_TTL)

        total = int(client.get(total_key) or 0)
        violated_cnt = int(client.get(violated_key) or 0)
        return True, {"total": total, "violated": violated_cnt}
    except Exception as e:
        logger.warning("Redis session event buffer failed: %s", e)
        return False, {"total": 0, "violated": 0}


def get_session_violation_stats_redis(session_id: str) -> Optional[dict]:
    """Redis에서 세션 위반 통계 조회"""
    client = get_redis_client()
    if not client:
        return None

    try:
        total = int(client.get(f"session:{session_id}:stats:total") or 0)
        violated = int(client.get(f"session:{session_id}:stats:violated") or 0)
        return {"total": total, "violated": violated}
    except Exception as e:
        logger.warning("Redis violation stats get failed: %s", e)
        return None


def flush_session_stats(session_id: str) -> None:
    """세션 종료 시 stats 키 삭제"""
    client = get_redis_client()
    if not client:
        return

    for key in (f"session:{session_id}:stats:total", f"session:{session_id}:stats:violated"):
        try:
            client.delete(key)
        except Exception as e:
            logger.warning("Redis stats flush failed: %s", e)
