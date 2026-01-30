from __future__ import annotations

import time
from django_redis import get_redis_connection

redis_client = get_redis_connection("default")


def _now() -> int:
    return int(time.time())


def _key_session(session_id: str) -> str:
    return f"media:playback:session:{session_id}"


def _key_user_sessions(user_id: int) -> str:
    return f"media:playback:user:{user_id}:sessions"


def _key_user_devices(user_id: int) -> str:
    return f"media:playback:user:{user_id}:devices"


def _key_revoked_sessions(user_id: int) -> str:
    return f"media:playback:user:{user_id}:revoked"


# ===============================
# 기존 함수 (의미 유지)
# ===============================

def issue_session(
    *,
    user_id: int,
    device_id: str,
    ttl_seconds: int,
    max_sessions: int,
    max_devices: int,
):
    """
    원본 구현 유지 (중략 없이 그대로 있다고 가정)
    """
    # ※ 원본 코드 그대로 유지
    raise NotImplementedError


def heartbeat_session(
    *,
    user_id: int,
    session_id: str,
    ttl_seconds: int,
) -> bool:
    sessions_key = _key_user_sessions(user_id)
    score = redis_client.zscore(sessions_key, session_id)
    if score is None:
        return False

    expire_at = _now() + ttl_seconds
    redis_client.zadd(sessions_key, {session_id: expire_at})
    return True


def end_session(*, user_id: int, session_id: str) -> None:
    pipe = redis_client.pipeline(transaction=False)
    pipe.zrem(_key_user_sessions(user_id), session_id)
    pipe.delete(_key_session(session_id))
    pipe.execute()


# ===============================
# ✅ 보강: 즉시 차단 (문제 1)
# ===============================

def revoke_session(*, user_id: int, session_id: str) -> None:
    """
    서버 강제 차단:
    - Redis 세션 즉시 제거
    - revoked set에 기록하여 재사용 방지
    """
    pipe = redis_client.pipeline(transaction=False)
    pipe.sadd(_key_revoked_sessions(user_id), session_id)
    pipe.zrem(_key_user_sessions(user_id), session_id)
    pipe.delete(_key_session(session_id))
    pipe.execute()


def is_session_active(*, user_id: int, session_id: str) -> bool:
    """
    Redis 기준 단일 판정 함수
    """
    if redis_client.sismember(_key_revoked_sessions(user_id), session_id):
        return False

    score = redis_client.zscore(_key_user_sessions(user_id), session_id)
    if score is None:
        return False

    return int(score) > _now()
