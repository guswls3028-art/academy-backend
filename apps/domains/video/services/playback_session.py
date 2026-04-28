# PATH: apps/support/video/services/playback_session.py

import uuid
from typing import Dict, Any, Tuple, Optional
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q, Count

from academy.adapters.db.django import repositories_video as video_repo
from apps.domains.enrollment.models import Enrollment
from apps.domains.video.models import VideoPlaybackSession

# Redis 보호 레이어 (선택적, 장애 시 DB fallback)
try:
    from libs.redis import is_redis_available
    from libs.redis.watch_buffer import (
        init_session_redis,
        buffer_heartbeat_session_ttl,
        buffer_session_event,
        get_session_violation_stats_redis,
        flush_session_stats,
        flush_session_buffer,
    )
except ImportError:
    def is_redis_available():
        return False
    def init_session_redis(*args, **kwargs):
        return False
    def buffer_heartbeat_session_ttl(*args, **kwargs):
        return False
    def buffer_session_event(*args, **kwargs):
        return False, {"total": 0, "violated": 0}
    def get_session_violation_stats_redis(*args, **kwargs):
        return None
    def flush_session_stats(*args, **kwargs):
        pass
    def flush_session_buffer(*args, **kwargs):
        return False


# =======================================================
# DB-based Session Management (Redis 제거됨)
# =======================================================

def _cleanup_expired_sessions(student_id: int) -> None:
    video_repo.playback_session_cleanup_expired(student_id)


def issue_session(
    *,
    student_id: int,
    device_id: str,
    ttl_seconds: int,
    max_sessions: int,
    max_devices: int,
) -> Tuple[bool, Dict[str, Any] | None, str | None]:
    """
    DB 기반 재생 세션 발급
    """
    if not device_id:
        return False, None, "device_id_required"

    _cleanup_expired_sessions(student_id)

    now = timezone.now()
    expires_at = now + timedelta(seconds=ttl_seconds)

    qs_active = video_repo.playback_session_filter_active(student_id, now, now)
    active_devices = qs_active.values_list("device_id", flat=True).distinct()
    unique_devices = set(active_devices)
    if device_id not in unique_devices and len(unique_devices) >= int(max_devices):
        return False, None, "device_limit_exceeded"

    active_count = qs_active.count()

    if active_count >= int(max_sessions):
        return False, None, "concurrency_limit_exceeded"

    session_id = str(uuid.uuid4())

    return True, {"session_id": session_id, "expires_at": int(expires_at.timestamp())}, None


def heartbeat_session(*, student_id: int, session_id: str, ttl_seconds: int) -> bool:
    """
    세션 TTL 연장
    - Redis 사용 시: DB 쓰기 없이 Redis에만 버퍼링 (Write-Behind)
    - Redis 미사용/장애 시: 기존 DB 기반 로직
    """
    if is_redis_available():
        ok = buffer_heartbeat_session_ttl(session_id=session_id, ttl_seconds=ttl_seconds)
        if ok:
            return True
        # Redis 실패 시 DB fallback

    now = timezone.now()
    new_expires_at = now + timedelta(seconds=ttl_seconds)

    try:
        session = video_repo.playback_session_get_by_session_id_and_student(session_id, student_id)
    except VideoPlaybackSession.DoesNotExist:
        return False

    if session.is_revoked:
        return False

    session.expires_at = new_expires_at
    session.last_seen = now
    session.save(update_fields=["expires_at", "last_seen"])

    return True


def end_session(*, student_id: int, session_id: str) -> None:
    """
    명시적 세션 종료
    - Redis 사용 시: Write-Behind flush (Redis stats → DB) 후 종료
    - Redis 미사용 시: 기존 DB 기반
    """
    now = timezone.now()

    if is_redis_available():
        stats = get_session_violation_stats_redis(session_id)
        if stats is not None:
            video_repo.playback_session_filter_update_active(
                session_id, student_id,
                last_seen=now,
                violated_count=stats.get("violated", 0),
                total_count=stats.get("total", 0),
                status=VideoPlaybackSession.Status.ENDED,
                ended_at=now,
            )
            flush_session_stats(session_id)
            flush_session_buffer(session_id)
            return

    video_repo.playback_session_filter_update_active(
        session_id, student_id,
        status=VideoPlaybackSession.Status.ENDED,
        ended_at=now,
    )


def revoke_session(*, student_id: int, session_id: str) -> None:
    """
    서버 강제 차단
    - Redis 사용 시: Write-Behind flush 후 차단
    """
    now = timezone.now()

    if is_redis_available():
        stats = get_session_violation_stats_redis(session_id)
        if stats is not None:
            video_repo.playback_session_filter_update_any(
                session_id, student_id,
                last_seen=now,
                violated_count=stats.get("violated", 0),
                total_count=stats.get("total", 0),
                status=VideoPlaybackSession.Status.REVOKED,
                is_revoked=True,
                ended_at=now,
            )
            flush_session_stats(session_id)
            flush_session_buffer(session_id)
            return

    video_repo.playback_session_filter_update_any(
        session_id, student_id,
        status=VideoPlaybackSession.Status.REVOKED,
        is_revoked=True,
        ended_at=now,
    )


def is_session_active(*, student_id: int, session_id: str) -> bool:
    """
    세션 활성 여부 확인
    - Redis 사용 시: session:{session_id}:meta 존재 여부 (heartbeat로 TTL 연장)
    - Redis 미사용/장애 시: DB 기반
    """
    if is_redis_available():
        try:
            from libs.redis.client import get_redis_client
            client = get_redis_client()
            if client and client.exists(f"session:{session_id}:meta"):
                return True
            # Redis에 키 없으면 DB fallback (세션 시작 직후 첫 heartbeat 전)
        except Exception:
            pass

    now = timezone.now()

    try:
        session = video_repo.playback_session_get_by_session_id_and_student_any(session_id, student_id)
    except VideoPlaybackSession.DoesNotExist:
        return False

    if session.is_revoked:
        return False

    if session.status != VideoPlaybackSession.Status.ACTIVE:
        return False

    if session.expires_at and session.expires_at <= now:
        session.status = VideoPlaybackSession.Status.EXPIRED
        session.ended_at = now
        session.save(update_fields=["status", "ended_at"])
        return False

    return True


# =======================================================
# 세션 단위 위반 누적 (DB 기반)
# =======================================================

def record_session_event(
    *,
    student_id: int,
    session_id: str,
    violated: bool,
    reason: str = "",
) -> Dict[str, int]:
    """
    세션 단위 누적 카운터
    - Redis 사용 시: DB 쓰기 없이 Redis에만 버퍼링 (Write-Behind)
    - Redis 미사용/장애 시: DB 기반
    """
    if is_redis_available():
        ok, stats = buffer_session_event(
            session_id=session_id,
            user_id=student_id,
            violated=violated,
            reason=reason,
        )
        if ok:
            return stats
        # Redis 실패 시 DB fallback

    try:
        session = video_repo.playback_session_get_by_session_id_and_student_any(session_id, student_id)
    except VideoPlaybackSession.DoesNotExist:
        return {"total": 0, "violated": 0}

    with transaction.atomic():
        session.refresh_from_db()
        session.total_count += 1
        if violated:
            session.violated_count += 1
        session.save(update_fields=["total_count", "violated_count"])

    return {
        "total": session.total_count,
        "violated": session.violated_count,
    }


def get_session_violation_stats(*, session_id: str) -> Dict[str, int]:
    """
    세션 위반 통계 조회
    - Redis 사용 시: Redis에서 조회 (실시간)
    - Redis 미사용/장애 시: DB 기반
    """
    if is_redis_available():
        stats = get_session_violation_stats_redis(session_id)
        if stats is not None:
            return stats

    try:
        session = video_repo.playback_session_get_by_session_id(session_id)
        return {
            "total": session.total_count,
            "violated": session.violated_count,
        }
    except VideoPlaybackSession.DoesNotExist:
        return {"total": 0, "violated": 0}


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

    video = video_repo.video_get_by_id_with_relations(video_id)
    enrollment = video_repo.enrollment_get_by_id_active_with_student_lecture(enrollment_id)

    # 🛡️ 안전 가드 (View 누락 방지용)
    if enrollment.lecture_id != video.session.lecture_id:
        return {
            "ok": False,
            "error": "enrollment_lecture_mismatch",
        }

    ttl = int(getattr(settings, "VIDEO_PLAYBACK_TTL_SECONDS", 600))

    ok, sess, err = issue_session(
        student_id=enrollment.student_id,
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
    expires_at_timestamp = int(sess["expires_at"])
    expires_at = timezone.datetime.fromtimestamp(expires_at_timestamp, tz=timezone.utc)

    video_repo.playback_session_create(
        video=video,
        enrollment=enrollment,
        session_id=session_id,
        device_id=device_id,
        status=VideoPlaybackSession.Status.ACTIVE,
        started_at=timezone.now(),
        expires_at=expires_at,
        last_seen=timezone.now(),
        violated_count=0,
        total_count=0,
        is_revoked=False,
    )

    return {
        "ok": True,
        "video_id": video.id,
        "enrollment_id": enrollment.id,
        "session_id": session_id,
        "expires_at": expires_at_timestamp,
    }
