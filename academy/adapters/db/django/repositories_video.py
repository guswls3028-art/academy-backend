"""
Video / Session / Enrollment 등 DB 조회 — .objects. 접근을 adapters 내부로 한정 (Gate 7).
"""
from __future__ import annotations

from typing import Optional


def get_video_status(video_id: int) -> Optional[str]:
    """Video 상태만 조회 (worker에서 이미 READY인지 확인용)."""
    from apps.support.video.models import Video
    row = Video.objects.filter(pk=video_id).values_list("status", flat=True).first()
    return row


def get_video_for_update(video_id: int):
    """select_for_update로 Video 1건 조회 (tenant_id 추출을 위한 select_related 포함)."""
    from apps.support.video.models import Video
    return Video.objects.select_for_update().select_related(
        "session", "session__lecture", "session__lecture__tenant"
    ).filter(id=int(video_id)).first()


def get_video_queryset_with_relations():
    """VideoViewSet 기본 queryset. upload_complete enqueue 시 video.session.lecture.tenant 필요."""
    from apps.support.video.models import Video
    return Video.objects.all().select_related(
        "session", "session__lecture", "session__lecture__tenant"
    )


def get_video_by_pk_with_relations(pk):
    """Video 1건 (session, lecture, tenant 포함). perform_destroy 등에서 tenant_id 사용."""
    from apps.support.video.models import Video
    return Video.objects.select_related(
        "session", "session__lecture", "session__lecture__tenant"
    ).filter(pk=pk).first()


def get_session_by_id_with_lecture_tenant(session_id):
    from apps.domains.lectures.models import Session
    return Session.objects.select_related("lecture", "lecture__tenant").get(id=session_id)


def create_video(session, title, file_key, order, status, allow_skip=False, max_speed=1.0, show_watermark=True):
    from apps.support.video.models import Video
    return Video.objects.create(
        session=session,
        title=title,
        file_key=file_key,
        order=order,
        status=status,
        allow_skip=allow_skip,
        max_speed=max_speed,
        show_watermark=show_watermark,
    )


def get_enrollments_for_lecture_active(lecture):
    from apps.domains.enrollment.models import Enrollment
    return Enrollment.objects.filter(lecture=lecture, status="ACTIVE").select_related("student")


def get_video_progresses_for_video(video):
    from apps.support.video.models import VideoProgress
    return VideoProgress.objects.filter(video=video)


def get_video_access_for_video(video):
    from apps.support.video.models import VideoAccess
    return VideoAccess.objects.filter(video=video)


def get_attendance_for_session(session):
    from apps.domains.attendance.models import Attendance
    return Attendance.objects.filter(session=session)


def get_enrollments_for_lecture(lecture):
    from apps.domains.enrollment.models import Enrollment
    return Enrollment.objects.filter(lecture=lecture)


def get_playback_events_queryset_for_video(video, since=None):
    from apps.support.video.models import VideoPlaybackEvent
    qs = VideoPlaybackEvent.objects.filter(video=video).select_related(
        "enrollment", "enrollment__student"
    )
    if since is not None:
        qs = qs.filter(occurred_at__gte=since)
    return qs


def video_filter_by_lecture(lecture):
    from apps.support.video.models import Video
    return Video.objects.filter(session__lecture=lecture).distinct()


def video_filter_by_session_ready(session_id):
    from apps.support.video.models import Video
    return Video.objects.filter(
        session_id=session_id,
        status=Video.Status.READY,
    ).order_by("order", "id")


def enrollment_get_by_student_lecture_active(student, lecture):
    from apps.domains.enrollment.models import Enrollment
    return Enrollment.objects.filter(
        student=student,
        lecture=lecture,
        status="ACTIVE",
    ).first()


def video_progress_get(video, enrollment):
    from apps.support.video.models import VideoProgress
    return VideoProgress.objects.filter(video=video, enrollment=enrollment).first()


def session_all_queryset():
    from apps.domains.lectures.models import Session
    return Session.objects.all()


def session_get_by_id_with_lecture(session_id):
    from apps.domains.lectures.models import Session
    return Session.objects.select_related("lecture").get(id=session_id)


def session_enrollment_exists(session, enrollment) -> bool:
    from apps.domains.enrollment.models import SessionEnrollment
    return SessionEnrollment.objects.filter(session=session, enrollment=enrollment).exists()


def video_access_get(video, enrollment):
    from apps.support.video.models import VideoAccess
    return VideoAccess.objects.filter(video=video, enrollment=enrollment).first()


def video_access_filter(video, enrollment=None):
    from apps.support.video.models import VideoAccess
    qs = VideoAccess.objects.filter(video=video)
    if enrollment is not None:
        qs = qs.filter(enrollment=enrollment)
    return qs


def video_access_update_or_create_by_ids(video_id, enrollment_id, defaults):
    from apps.support.video.models import VideoAccess
    return VideoAccess.objects.update_or_create(
        video_id=video_id,
        enrollment_id=enrollment_id,
        defaults=defaults,
    )


def video_access_all():
    from apps.support.video.models import VideoAccess
    return VideoAccess.objects.all()


def video_progress_all():
    from apps.support.video.models import VideoProgress
    return VideoProgress.objects.all()


def video_progress_filter(video):
    from apps.support.video.models import VideoProgress
    return VideoProgress.objects.filter(video=video)


def video_progress_filter_video_enrollment_ids(video, enrollment_ids):
    from apps.support.video.models import VideoProgress
    qs = VideoProgress.objects.filter(enrollment_id__in=enrollment_ids)
    if video:
        qs = qs.filter(video=video)
    return qs


def video_get_by_id(video_id):
    from apps.support.video.models import Video
    return Video.objects.filter(id=int(video_id)).first()


def video_get_by_id_only_policy(video_id):
    from apps.support.video.models import Video
    return Video.objects.filter(id=video_id).only("id", "policy_version").first()


def video_get_by_id_with_relations(video_id):
    from apps.support.video.models import Video
    return Video.objects.select_related("session", "session__lecture").get(id=video_id)


def video_get_by_id_with_session(video_id):
    from apps.support.video.models import Video
    return Video.objects.select_related("session").get(id=video_id)


def video_update(video_id, **kwargs):
    from apps.support.video.models import Video
    return Video.objects.filter(id=video_id).update(**kwargs)


def enrollment_filter_by_lecture_active(lecture):
    from apps.domains.enrollment.models import Enrollment
    return Enrollment.objects.filter(lecture=lecture, status="ACTIVE").select_related("student")


def enrollment_get_by_id(enrollment_id):
    from apps.domains.enrollment.models import Enrollment
    return Enrollment.objects.filter(id=enrollment_id).first()


def enrollment_get_by_id_with_relations(enrollment_id):
    from apps.domains.enrollment.models import Enrollment
    return Enrollment.objects.select_related("lecture").get(id=enrollment_id)


def enrollment_get_by_id_active_with_student_lecture(enrollment_id):
    from apps.domains.enrollment.models import Enrollment
    return Enrollment.objects.select_related("student", "lecture").get(
        id=enrollment_id,
        status="ACTIVE",
    )


def enrollment_select_related_lecture_filter(id=None):
    from apps.domains.enrollment.models import Enrollment
    qs = Enrollment.objects.select_related("lecture")
    if id is not None:
        qs = qs.filter(id=id)
    return qs


def attendance_filter_session(session):
    from apps.domains.attendance.models import Attendance
    return Attendance.objects.filter(session=session)


def attendance_filter_session_enrollment(session, enrollment):
    from apps.domains.attendance.models import Attendance
    return Attendance.objects.filter(session=session, enrollment=enrollment)


def attendance_filter_session_status(session, status):
    from apps.domains.attendance.models import Attendance
    return Attendance.objects.filter(session=session, status=status)


# ---- VideoPlaybackSession ----
def playback_session_cleanup_expired(student_id):
    from django.utils import timezone
    from apps.support.video.models import VideoPlaybackSession
    now = timezone.now()
    return VideoPlaybackSession.objects.filter(
        enrollment__student_id=student_id,
        status=VideoPlaybackSession.Status.ACTIVE,
        expires_at__lt=now,
    ).update(status=VideoPlaybackSession.Status.EXPIRED, ended_at=now)


def playback_session_filter_active(student_id, now, expires_at_gt):
    from apps.support.video.models import VideoPlaybackSession
    return VideoPlaybackSession.objects.filter(
        enrollment__student_id=student_id,
        status=VideoPlaybackSession.Status.ACTIVE,
        expires_at__gt=expires_at_gt,
    )


def playback_session_create(**kwargs):
    from apps.support.video.models import VideoPlaybackSession
    return VideoPlaybackSession.objects.create(**kwargs)


def playback_session_get_by_session_id(session_id):
    from apps.support.video.models import VideoPlaybackSession
    return VideoPlaybackSession.objects.get(session_id=session_id)


def playback_session_filter_update_active(session_id, student_id, **update_kwargs):
    from apps.support.video.models import VideoPlaybackSession
    return VideoPlaybackSession.objects.filter(
        session_id=session_id,
        enrollment__student_id=student_id,
        status=VideoPlaybackSession.Status.ACTIVE,
    ).update(**update_kwargs)


def playback_session_select_related_get(session_id):
    from apps.support.video.models import VideoPlaybackSession
    return VideoPlaybackSession.objects.select_related(
        "enrollment", "enrollment__student", "video"
    ).get(session_id=session_id)


def playback_session_select_related_filter(**kwargs):
    from apps.support.video.models import VideoPlaybackSession
    return VideoPlaybackSession.objects.select_related(
        "enrollment", "enrollment__student", "video"
    ).filter(**kwargs)


def playback_session_filter(**kwargs):
    from apps.support.video.models import VideoPlaybackSession
    return VideoPlaybackSession.objects.filter(**kwargs)


def playback_session_end_by_session_id(session_id):
    from django.utils import timezone
    from apps.support.video.models import VideoPlaybackSession
    now = timezone.now()
    return VideoPlaybackSession.objects.filter(session_id=session_id).update(
        status=VideoPlaybackSession.Status.ENDED,
        ended_at=now,
    )


def playback_session_get_by_session_id_and_student(session_id, student_id):
    from apps.support.video.models import VideoPlaybackSession
    return VideoPlaybackSession.objects.select_related("enrollment").get(
        session_id=session_id,
        enrollment__student_id=student_id,
        status=VideoPlaybackSession.Status.ACTIVE,
    )


def playback_session_get_by_session_id_and_student_any(session_id, student_id):
    from apps.support.video.models import VideoPlaybackSession
    return VideoPlaybackSession.objects.select_related("enrollment").get(
        session_id=session_id,
        enrollment__student_id=student_id,
    )


def playback_session_filter_update(session_id, student_id, **update_kwargs):
    from apps.support.video.models import VideoPlaybackSession
    return VideoPlaybackSession.objects.filter(
        session_id=session_id,
        enrollment__student_id=student_id,
        status=VideoPlaybackSession.Status.ACTIVE,
    ).update(**update_kwargs)


def playback_session_filter_update_any(session_id, student_id, **update_kwargs):
    from django.utils import timezone
    from apps.support.video.models import VideoPlaybackSession
    now = timezone.now()
    return VideoPlaybackSession.objects.filter(
        session_id=session_id,
        enrollment__student_id=student_id,
    ).update(**update_kwargs)


def playback_session_update_expired(now):
    from apps.support.video.models import VideoPlaybackSession
    return VideoPlaybackSession.objects.filter(
        status=VideoPlaybackSession.Status.ACTIVE,
        expires_at__lt=now,
    ).update(status=VideoPlaybackSession.Status.EXPIRED, ended_at=now)


# ---- VideoPlaybackEvent ----
def playback_event_filter_by_video_id(video_id, since=None):
    from apps.support.video.models import VideoPlaybackEvent
    qs = VideoPlaybackEvent.objects.filter(video_id=video_id).select_related(
        "enrollment", "enrollment__student"
    )
    if since is not None:
        qs = qs.filter(occurred_at__gte=since)
    return qs


def playback_event_bulk_create(objs, batch_size=500):
    from apps.support.video.models import VideoPlaybackEvent
    return VideoPlaybackEvent.objects.bulk_create(objs, batch_size=batch_size)


# ---- Video Worker용 Repository (IVideoRepository 호환, Gate 7: ORM을 adapters 내부로) ----


def _tenant_id_from_video(video) -> Optional[int]:
    """Video에서 tenant_id 추출 (session.lecture.tenant)."""
    if not video:
        return None
    if getattr(video, "session", None) and getattr(video.session, "lecture", None):
        return getattr(video.session.lecture, "tenant_id", None)
    return None


def _cache_video_status_safe(
    video_id: int,
    tenant_id: Optional[int],
    status: str,
    *,
    hls_path: Optional[str] = None,
    duration: Optional[int] = None,
    error_reason: Optional[str] = None,
    ttl: Optional[int] = None,
) -> None:
    """Redis에 비디오 상태 기록 (Worker 완료/실패 시 progress API가 동일 상태 반환하도록)."""
    if not tenant_id:
        return
    try:
        from apps.support.video.redis_status_cache import cache_video_status
        cache_video_status(
            tenant_id=tenant_id,
            video_id=video_id,
            status=status,
            hls_path=hls_path,
            duration=duration,
            error_reason=error_reason,
            ttl=ttl,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to cache video status in Redis: %s", e)


class DjangoVideoRepository:
    """
    Video 상태 업데이트 — mark_processing / complete_video / fail_video.
    Worker는 이 adapter만 사용 (src.infrastructure.db.video_repository 대체).
    완료/실패 시 Redis에도 동기화하여 progress API(GET /media/videos/{id}/progress/)가
    READY/FAILED를 반환하도록 함.
    """

    def mark_processing(self, video_id: int) -> bool:
        from django.db import transaction
        from django.utils import timezone
        from apps.support.video.models import Video

        with transaction.atomic():
            video = get_video_for_update(video_id)
            if not video:
                return False
            if video.status == Video.Status.PROCESSING:
                return True
            if video.status != Video.Status.UPLOADED:
                import logging
                logging.getLogger(__name__).warning(
                    "Cannot mark video %s as PROCESSING: status=%s", video_id, video.status,
                )
                return False
            video.status = Video.Status.PROCESSING
            if hasattr(video, "processing_started_at"):
                video.processing_started_at = timezone.now()
            update_fields = ["status"]
            if hasattr(video, "processing_started_at"):
                update_fields.append("processing_started_at")
            video.save(update_fields=update_fields)
            tenant_id = _tenant_id_from_video(video)
        _cache_video_status_safe(
            video_id, tenant_id,
            getattr(Video.Status.PROCESSING, "value", "PROCESSING"),
            ttl=21600,
        )
        return True

    def try_claim_video(
        self, video_id: int, worker_id: str, lease_seconds: int = 14400
    ) -> bool:
        """
        UPLOADED → PROCESSING 원자 변경 + leased_by, leased_until 설정.
        이미 PROCESSING/READY면 False (다른 워커가 처리 중이거나 완료).
        빠른 ACK + DB lease 패턴용.
        """
        from django.db import transaction
        from django.utils import timezone
        from datetime import timedelta
        from apps.support.video.models import Video

        with transaction.atomic():
            video = get_video_for_update(video_id)
            if not video:
                return False
            if video.status == Video.Status.PROCESSING:
                return False
            if video.status == Video.Status.READY:
                return False
            if video.status != Video.Status.UPLOADED:
                import logging
                logging.getLogger(__name__).warning(
                    "try_claim_video: video %s status=%s (expected UPLOADED)",
                    video_id,
                    video.status,
                )
                return False
            video.status = Video.Status.PROCESSING
            if hasattr(video, "processing_started_at"):
                video.processing_started_at = timezone.now()
            video.leased_by = str(worker_id)[:64]
            video.leased_until = timezone.now() + timedelta(seconds=lease_seconds)
            update_fields = ["status", "leased_by", "leased_until"]
            if hasattr(video, "processing_started_at"):
                update_fields.append("processing_started_at")
            video.save(update_fields=update_fields)
            tenant_id = _tenant_id_from_video(video)
        _cache_video_status_safe(
            video_id, tenant_id,
            getattr(Video.Status.PROCESSING, "value", "PROCESSING"),
            ttl=21600,
        )
        return True

    def try_reclaim_video(self, video_id: int) -> bool:
        """
        PROCESSING 이지만 leased_until < now 인 경우 UPLOADED로 되돌림.
        Re-enqueue 후 다른 워커가 try_claim 가능.
        """
        from django.db import transaction
        from django.utils import timezone
        from apps.support.video.models import Video

        with transaction.atomic():
            video = get_video_for_update(video_id)
            if not video:
                return False
            if video.status != Video.Status.PROCESSING:
                return False
            if video.leased_until is None or video.leased_until >= timezone.now():
                return False
            video.status = Video.Status.UPLOADED
            video.leased_by = ""
            video.leased_until = None
            video.save(update_fields=["status", "leased_by", "leased_until"])
        return True

    def complete_video(
        self,
        video_id: int,
        hls_path: str,
        duration: Optional[int] = None,
    ) -> tuple[bool, str]:
        from django.db import transaction
        from apps.support.video.models import Video

        with transaction.atomic():
            video = get_video_for_update(video_id)
            if not video:
                import logging
                _log = logging.getLogger(__name__)
                exists = Video.objects.filter(pk=video_id).exists()
                _log.warning(
                    "complete_video: video_id=%s not found (row exists=%s). "
                    "Possible: row/parent deleted during encode, or worker DB differs from API.",
                    video_id,
                    exists,
                )
                return False, "not_found"
            if video.status == Video.Status.READY and bool(video.hls_path):
                return True, "idempotent"
            if video.status != Video.Status.PROCESSING:
                import logging
                logging.getLogger(__name__).warning(
                    "Video %s status is %s (expected PROCESSING)", video_id, video.status,
                )
            video.hls_path = str(hls_path)
            if duration is not None and duration >= 0:
                video.duration = int(duration)
            video.status = Video.Status.READY
            if hasattr(video, "leased_until"):
                video.leased_until = None
            if hasattr(video, "leased_by"):
                video.leased_by = ""
            update_fields = ["hls_path", "status"]
            if duration is not None and duration >= 0:
                update_fields.append("duration")
            if hasattr(video, "leased_until"):
                update_fields.append("leased_until")
            if hasattr(video, "leased_by"):
                update_fields.append("leased_by")
            video.save(update_fields=update_fields)
            tenant_id = _tenant_id_from_video(video)
        _cache_video_status_safe(
            video_id, tenant_id,
            getattr(Video.Status.READY, "value", "READY"),
            hls_path=str(hls_path),
            duration=int(duration) if duration is not None and duration >= 0 else None,
            ttl=None,
        )
        return True, "ok"

    def fail_video(self, video_id: int, reason: str) -> tuple[bool, str]:
        from django.db import transaction
        from apps.support.video.models import Video

        with transaction.atomic():
            video = get_video_for_update(video_id)
            if not video:
                return False, "not_found"
            if video.status == Video.Status.FAILED:
                return True, "idempotent"
            video.status = Video.Status.FAILED
            if hasattr(video, "error_reason"):
                video.error_reason = str(reason)[:2000]
            if hasattr(video, "leased_until"):
                video.leased_until = None
            if hasattr(video, "leased_by"):
                video.leased_by = ""
            update_fields = ["status"]
            if hasattr(video, "error_reason"):
                update_fields.append("error_reason")
            if hasattr(video, "leased_until"):
                update_fields.append("leased_until")
            if hasattr(video, "leased_by"):
                update_fields.append("leased_by")
            video.save(update_fields=update_fields)
            tenant_id = _tenant_id_from_video(video)
        _cache_video_status_safe(
            video_id, tenant_id,
            getattr(Video.Status.FAILED, "value", "FAILED"),
            error_reason=str(reason)[:2000],
            ttl=None,
        )
        return True, "ok"
