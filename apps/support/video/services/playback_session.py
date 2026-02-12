# PATH: apps/support/video/services/playback_session.py

import uuid
from typing import Dict, Any, Tuple, Optional
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q, Count

from apps.domains.enrollment.models import Enrollment
from apps.support.video.models import (
    Video,
    VideoPlaybackSession,
)


# =======================================================
# DB-based Session Management (Redis 제거됨)
# =======================================================

def _cleanup_expired_sessions(student_id: int) -> None:
    """
    만료된 세션 정리 (DB 기반)
    """
    now = timezone.now()
    VideoPlaybackSession.objects.filter(
        enrollment__student_id=student_id,
        status=VideoPlaybackSession.Status.ACTIVE,
        expires_at__lt=now,
    ).update(
        status=VideoPlaybackSession.Status.EXPIRED,
        ended_at=now,
    )


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

    # 기기 제한 확인
    active_devices = VideoPlaybackSession.objects.filter(
        enrollment__student_id=student_id,
        status=VideoPlaybackSession.Status.ACTIVE,
        expires_at__gt=now,
    ).values_list("device_id", flat=True).distinct()

    unique_devices = set(active_devices)
    if device_id not in unique_devices and len(unique_devices) >= int(max_devices):
        return False, None, "device_limit_exceeded"

    # 동시 세션 제한 확인
    active_count = VideoPlaybackSession.objects.filter(
        enrollment__student_id=student_id,
        status=VideoPlaybackSession.Status.ACTIVE,
        expires_at__gt=now,
    ).count()

    if active_count >= int(max_sessions):
        return False, None, "concurrency_limit_exceeded"

    session_id = str(uuid.uuid4())

    return True, {"session_id": session_id, "expires_at": int(expires_at.timestamp())}, None


def heartbeat_session(*, student_id: int, session_id: str, ttl_seconds: int) -> bool:
    """
    세션 TTL 연장 (DB 기반)
    - student_id 소유 검증 포함
    """
    now = timezone.now()
    new_expires_at = now + timedelta(seconds=ttl_seconds)

    try:
        session = VideoPlaybackSession.objects.select_related(
            "enrollment"
        ).get(
            session_id=session_id,
            enrollment__student_id=student_id,
            status=VideoPlaybackSession.Status.ACTIVE,
        )
    except VideoPlaybackSession.DoesNotExist:
        return False

    if session.is_revoked:
        return False

    # TTL 연장
    session.expires_at = new_expires_at
    session.last_seen = now
    session.save(update_fields=["expires_at", "last_seen"])

    return True


def end_session(*, student_id: int, session_id: str) -> None:
    """
    명시적 세션 종료 (DB 기반)
    """
    VideoPlaybackSession.objects.filter(
        session_id=session_id,
        enrollment__student_id=student_id,
        status=VideoPlaybackSession.Status.ACTIVE,
    ).update(
        status=VideoPlaybackSession.Status.ENDED,
        ended_at=timezone.now(),
    )


def revoke_session(*, student_id: int, session_id: str) -> None:
    """
    서버 강제 차단 (DB 기반)
    """
    VideoPlaybackSession.objects.filter(
        session_id=session_id,
        enrollment__student_id=student_id,
    ).update(
        status=VideoPlaybackSession.Status.REVOKED,
        is_revoked=True,
        ended_at=timezone.now(),
    )


def is_session_active(*, student_id: int, session_id: str) -> bool:
    """
    세션 활성 여부 확인 (DB 기반)
    """
    now = timezone.now()
    
    try:
        session = VideoPlaybackSession.objects.select_related(
            "enrollment"
        ).get(
            session_id=session_id,
            enrollment__student_id=student_id,
        )
    except VideoPlaybackSession.DoesNotExist:
        return False

    if session.is_revoked:
        return False

    if session.status != VideoPlaybackSession.Status.ACTIVE:
        return False

    if session.expires_at and session.expires_at <= now:
        # 만료 처리
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
    세션 단위 누적 카운터 (DB 기반)
    """
    try:
        session = VideoPlaybackSession.objects.select_related(
            "enrollment"
        ).get(
            session_id=session_id,
            enrollment__student_id=student_id,
        )
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
    세션 위반 통계 조회 (DB 기반)
    """
    try:
        session = VideoPlaybackSession.objects.get(session_id=session_id)
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

    VideoPlaybackSession.objects.create(
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
