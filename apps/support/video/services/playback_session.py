# PATH: apps/support/video/services/playback_session.py

import time
import uuid
from typing import Dict, Any, Tuple, Iterable, Optional

from django.conf import settings
from django.utils import timezone

from libs.redis_client.client import redis_client

from apps.domains.enrollment.models import Enrollment
from apps.support.video.models import (
    Video,
    VideoPlaybackSession,
)

# =======================================================
# Redis Key Helpers
# =======================================================

def _now() -> int:
    return int(time.time())


def _key_user_sessions(user_id: int) -> str:
    # zset (session_id -> expires_at)
    return f"media:playback:user:{int(user_id)}:sessions"


def _key_session(session_id: str) -> str:
    # hash
    return f"media:playback:session:{session_id}"


def _key_user_devices(user_id: int) -> str:
    # set
    return f"media:playback:user:{int(user_id)}:devices"


def _key_user_revoked(user_id: int) -> str:
    # set
    return f"media:playback:user:{int(user_id)}:revoked"


# ✅ 문제 1/7: 세션 단위 위반 누적 카운터
def _key_session_violation(session_id: str) -> str:
    # hash: { violated, total, last_reason }
    return f"media:playback:session:{session_id}:violation"


# =======================================================
# Decode Helpers (bytes/str 방어)
# =======================================================

def _decode(value):
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return value.decode()
    return value


def _decode_set(values: Iterable) -> set[str]:
    return {_decode(v) for v in (values or set())}


# =======================================================
# Internal Helpers
# =======================================================

def _cleanup_expired_sessions(user_id: int) -> None:
    """
    만료된 세션 정리
    - session zset
    - session hash
    - device set (누수 방지)
    """
    sessions_key = _key_user_sessions(user_id)
    devices_key = _key_user_devices(user_id)

    now = _now()
    expired = redis_client.zrangebyscore(sessions_key, 0, now)
    if not expired:
        return

    pipe = redis_client.pipeline(transaction=False)

    for raw_sid in expired:
        sid = _decode(raw_sid)

        device_id = _decode(redis_client.hget(_key_session(sid), "device_id"))
        if device_id:
            pipe.srem(devices_key, device_id)

        pipe.zrem(sessions_key, sid)
        pipe.delete(_key_session(sid))

        # violation hash도 같이 제거(누수 방지)
        pipe.delete(_key_session_violation(sid))

    pipe.execute()


def _session_ttl_seconds_from_expires_at(expires_at: int) -> int:
    now = _now()
    ttl = max(0, int(expires_at) - now)
    # 보호: 너무 짧으면 최소 30s
    return max(30, ttl)


# =======================================================
# Core Session APIs (Redis / Infra)
# =======================================================

def issue_session(
    *,
    user_id: int,
    device_id: str,
    ttl_seconds: int,
    max_sessions: int,
    max_devices: int,
) -> Tuple[bool, Dict[str, Any] | None, str | None]:
    """
    Redis 기반 재생 세션 발급 (공통 Infra)
    """
    if not device_id:
        return False, None, "device_id_required"

    _cleanup_expired_sessions(user_id)

    sessions_key = _key_user_sessions(user_id)
    devices_key = _key_user_devices(user_id)

    now = _now()
    expires_at = now + int(ttl_seconds)

    existing_devices = _decode_set(redis_client.smembers(devices_key))

    # 기기 제한
    if device_id not in existing_devices and len(existing_devices) >= int(max_devices):
        return False, None, "device_limit_exceeded"

    # 동시 세션 제한
    active_count = int(redis_client.zcard(sessions_key) or 0)
    if active_count >= int(max_sessions):
        return False, None, "concurrency_limit_exceeded"

    session_id = str(uuid.uuid4())

    pipe = redis_client.pipeline(transaction=True)

    pipe.zadd(sessions_key, {session_id: expires_at})
    pipe.expire(sessions_key, ttl_seconds + 60)

    pipe.hset(
        _key_session(session_id),
        mapping={
            "user_id": str(user_id),
            "device_id": device_id,
            "expires_at": str(expires_at),
            "last_seen": str(now),
        },
    )
    pipe.expire(_key_session(session_id), ttl_seconds + 60)

    pipe.sadd(devices_key, device_id)
    pipe.expire(devices_key, ttl_seconds + 60)

    # violation state init
    pipe.hset(
        _key_session_violation(session_id),
        mapping={
            "violated": "0",
            "total": "0",
            "last_reason": "",
        },
    )
    pipe.expire(_key_session_violation(session_id), ttl_seconds + 60)

    pipe.execute()

    return True, {"session_id": session_id, "expires_at": expires_at}, None


def heartbeat_session(*, user_id: int, session_id: str, ttl_seconds: int) -> bool:
    """
    세션 TTL 연장
    - user_id 소유 검증 포함
    - revoked면 False
    """
    session_id = _decode(session_id)

    if redis_client.sismember(_key_user_revoked(user_id), session_id):
        return False

    sid_key = _key_session(session_id)
    sessions_key = _key_user_sessions(user_id)

    owner = _decode(redis_client.hget(sid_key, "user_id"))
    if not owner or int(owner) != int(user_id):
        return False

    now = _now()
    new_expires_at = now + int(ttl_seconds)

    pipe = redis_client.pipeline(transaction=True)
    pipe.zadd(sessions_key, {session_id: new_expires_at})
    pipe.hset(
        sid_key,
        mapping={
            "expires_at": str(new_expires_at),
            "last_seen": str(now),
        },
    )
    pipe.expire(sessions_key, ttl_seconds + 60)
    pipe.expire(sid_key, ttl_seconds + 60)

    # violation ttl도 함께 연장
    pipe.expire(_key_session_violation(session_id), ttl_seconds + 60)

    pipe.execute()

    return True


def end_session(*, user_id: int, session_id: str) -> None:
    """
    명시적 세션 종료
    """
    session_id = _decode(session_id)

    sessions_key = _key_user_sessions(user_id)
    devices_key = _key_user_devices(user_id)
    sid_key = _key_session(session_id)

    device_id = _decode(redis_client.hget(sid_key, "device_id"))

    pipe = redis_client.pipeline(transaction=False)
    pipe.zrem(sessions_key, session_id)
    pipe.delete(sid_key)
    pipe.delete(_key_session_violation(session_id))

    if device_id:
        pipe.srem(devices_key, device_id)

    pipe.execute()


def revoke_session(*, user_id: int, session_id: str) -> None:
    """
    ✅ 문제 1: 서버 강제 차단
    - revoked set에 기록(재사용 방지)
    - 세션 자료 즉시 제거
    - violation hash 제거(누수 방지)
    """
    session_id = _decode(session_id)

    sessions_key = _key_user_sessions(user_id)
    devices_key = _key_user_devices(user_id)
    sid_key = _key_session(session_id)

    device_id = _decode(redis_client.hget(sid_key, "device_id"))

    pipe = redis_client.pipeline(transaction=False)
    pipe.sadd(_key_user_revoked(user_id), session_id)
    pipe.zrem(sessions_key, session_id)
    pipe.delete(sid_key)
    pipe.delete(_key_session_violation(session_id))

    if device_id:
        pipe.srem(devices_key, device_id)

    # revoked set 누수 방지: TTL (세션 TTL 알 수 없으면 기본 1h)
    try:
        pipe.expire(_key_user_revoked(user_id), int(getattr(settings, "VIDEO_REVOKED_SET_TTL_SECONDS", 3600)))
    except Exception:
        pass

    pipe.execute()


def is_session_active(*, user_id: int, session_id: str) -> bool:
    """
    세션 활성 여부 확인
    """
    session_id = _decode(session_id)

    if redis_client.sismember(_key_user_revoked(user_id), session_id):
        return False

    sessions_key = _key_user_sessions(user_id)
    score = redis_client.zscore(sessions_key, session_id)
    if score is None:
        return False

    return int(score) > _now()


# =======================================================
# ✅ 문제 1/7: 세션 단위 위반 누적(우회 불가)
# =======================================================

def record_session_event(
    *,
    user_id: int,
    session_id: str,
    violated: bool,
    reason: str = "",
) -> Dict[str, int]:
    """
    세션 단위 누적 카운터:
    - total +1
    - violated이면 violated +1
    - TTL은 세션 expires_at 기반으로 동기화
    """
    session_id = _decode(session_id)

    # 세션 expires_at 읽기(없으면 default TTL 사용)
    expires_at_raw = _decode(redis_client.hget(_key_session(session_id), "expires_at"))
    ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))
    if expires_at_raw:
        try:
            ttl = _session_ttl_seconds_from_expires_at(int(expires_at_raw))
        except Exception:
            ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))

    key = _key_session_violation(session_id)

    pipe = redis_client.pipeline(transaction=True)
    pipe.hincrby(key, "total", 1)
    if violated:
        pipe.hincrby(key, "violated", 1)
        if reason:
            pipe.hset(key, "last_reason", str(reason)[:200])
    pipe.expire(key, ttl + 60)
    res = pipe.execute()

    # res[0]=total, res[1]=violated or total, etc → 안전 파싱
    total = 0
    violated_cnt = 0
    try:
        # total always first
        total = int(res[0] or 0)
        # if violated, second is violated; else second is expire result
        if violated:
            violated_cnt = int(res[1] or 0)
        else:
            violated_cnt = int(_decode(redis_client.hget(key, "violated")) or 0)
    except Exception:
        # fallback fetch
        try:
            total = int(_decode(redis_client.hget(key, "total")) or 0)
            violated_cnt = int(_decode(redis_client.hget(key, "violated")) or 0)
        except Exception:
            total = 0
            violated_cnt = 0

    return {"total": total, "violated": violated_cnt}


def get_session_violation_stats(*, session_id: str) -> Dict[str, int]:
    session_id = _decode(session_id)
    key = _key_session_violation(session_id)
    data = redis_client.hgetall(key) or {}
    try:
        total = int(_decode(data.get(b"total") or data.get("total") or 0))
    except Exception:
        total = 0
    try:
        violated = int(_decode(data.get(b"violated") or data.get("violated") or 0))
    except Exception:
        violated = 0
    return {"total": total, "violated": violated}


def should_revoke_by_stats(*, violated: int, total: int) -> bool:
    """
    보수적 차단 기준:
    - violated >= threshold
    - 또는 violated/total 비율이 너무 높으면 차단
    """
    threshold = int(getattr(settings, "VIDEO_VIOLATION_REVOKE_THRESHOLD", 3))
    ratio = float(getattr(settings, "VIDEO_VIOLATION_REVOKE_RATIO", 0.5))

    if int(violated) >= threshold:
        return True
    if int(total) > 0 and (float(violated) / float(total)) >= ratio:
        return True
    return False


# =======================================================
# Facade API (Student ONLY) - 기존 유지
# =======================================================

def create_playback_session(
    *,
    user,
    video_id: int,
    enrollment_id: int,
    device_id: str,
) -> dict:
    """
    학생 전용 Facade API

    책임:
    - "재생 세션 생성"만 담당
    - 권한 / 수강 검증은 View에서 선행되어야 함
    """

    # 🚫 강사 / 운영자 차단
    if getattr(user, "is_instructor", False) or getattr(user, "is_staff", False):
        return {
            "ok": False,
            "error": "instructor_must_use_play_api",
        }

    if not device_id:
        return {"ok": False, "error": "device_id_required"}

    video = Video.objects.select_related(
        "session",
        "session__lecture",
    ).get(id=video_id)

    enrollment = Enrollment.objects.select_related(
        "student",
        "lecture",
    ).get(
        id=enrollment_id,
        status="ACTIVE",
    )

    # 🛡️ 안전 가드 (View 누락 방지용)
    if enrollment.lecture_id != video.session.lecture_id:
        return {
            "ok": False,
            "error": "enrollment_lecture_mismatch",
        }

    ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))

    ok, sess, err = issue_session(
        user_id=user.id,
        device_id=device_id,
        ttl_seconds=ttl,
        max_sessions=int(getattr(settings, "VIDEO_MAX_SESSIONS", 9999)),
        max_devices=int(getattr(settings, "VIDEO_MAX_DEVICES", 9999)),
    )

    if not ok:
        return {
            "ok": False,
            "error": err,
        }

    session_id = str(sess["session_id"])
    expires_at = int(sess["expires_at"])

    VideoPlaybackSession.objects.create(
        video=video,
        enrollment=enrollment,
        session_id=session_id,
        device_id=device_id,
        status=VideoPlaybackSession.Status.ACTIVE,
        started_at=timezone.now(),
    )

    return {
        "ok": True,
        "video_id": video.id,
        "enrollment_id": enrollment.id,
        "session_id": session_id,
        "expires_at": expires_at,
    }
