import time
import uuid
from typing import Dict, Any, Tuple, Iterable

from django.conf import settings
from django.utils import timezone

from libs.redis_client.client import redis_client

from apps.domains.enrollment.models import Enrollment
from apps.support.media.models import (
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
    return f"media:playback:user:{user_id}:sessions"


def _key_session(session_id: str) -> str:
    # hash
    return f"media:playback:session:{session_id}"


def _key_user_devices(user_id: int) -> str:
    # set
    return f"media:playback:user:{user_id}:devices"


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

        device_id = _decode(
            redis_client.hget(_key_session(sid), "device_id")
        )
        if device_id:
            pipe.srem(devices_key, device_id)

        pipe.zrem(sessions_key, sid)
        pipe.delete(_key_session(sid))

    pipe.execute()


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

    existing_devices = _decode_set(
        redis_client.smembers(devices_key)
    )

    if device_id not in existing_devices and len(existing_devices) >= int(max_devices):
        return False, None, "device_limit_exceeded"

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

    pipe.execute()

    return True, {"session_id": session_id, "expires_at": expires_at}, None


def heartbeat_session(*, user_id: int, session_id: str, ttl_seconds: int) -> bool:
    """
    세션 TTL 연장
    - user_id 소유 검증 포함
    """
    session_id = _decode(session_id)
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

    if device_id:
        pipe.srem(devices_key, device_id)

    pipe.execute()


def is_session_active(*, user_id: int, session_id: str) -> bool:
    """
    세션 활성 여부 확인
    """
    session_id = _decode(session_id)
    sessions_key = _key_user_sessions(user_id)

    score = redis_client.zscore(sessions_key, session_id)
    if score is None:
        return False

    return int(score) > _now()


# =======================================================
# Facade API (Student ONLY)
# =======================================================

def create_playback_session(*, user, video_id: int, enrollment_id: int) -> dict:
    """
    학생 전용 Facade API

    책임:
    - "재생 세션 생성"만 담당
    - 권한 / 수강 검증은 반드시 View에서 선행되어야 함

    ⚠️ 주의
    - 이 함수는 Facade View 전용이다.
    - 다른 View / Task / Script에서 직접 호출하지 말 것.
    """

    # 🚫 강사 / 운영자 차단
    if getattr(user, "is_instructor", False) or getattr(user, "is_staff", False):
        return {
            "ok": False,
            "error": "instructor_must_use_play_api",
        }

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

    # 🛡️ 안전 가드 (View 누락 방지용, 정상 경로에서는 항상 통과)
    if enrollment.lecture_id != video.session.lecture_id:
        return {
            "ok": False,
            "error": "enrollment_lecture_mismatch",
        }

    ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))

    # Facade 전용 논리 device
    device_id = f"facade:{uuid.uuid4()}"

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
