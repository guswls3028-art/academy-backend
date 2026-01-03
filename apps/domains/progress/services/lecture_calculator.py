# apps/domains/progress/services/lecture_calculator.py
from __future__ import annotations

from django.utils import timezone

from apps.domains.progress.models import LectureProgress, SessionProgress
from apps.domains.lectures.models import Lecture


class LectureProgressCalculator:
    """
    강의 단위 집계 계산기
    """

    @staticmethod
    def calculate(*, enrollment_id: int, lecture: Lecture) -> LectureProgress:
        sessions = lecture.sessions.all()
        total_sessions = sessions.count()

        progress_qs = SessionProgress.objects.filter(
            enrollment_id=enrollment_id,
            session__lecture=lecture,
        ).order_by("session__order")

        completed_sessions = progress_qs.filter(completed=True).count()
        failed_sessions = progress_qs.filter(completed=False).count()

        # 연속 미완료 계산
        consecutive_failed = 0
        for p in progress_qs.reverse():
            if p.completed:
                break
            consecutive_failed += 1

        obj, _ = LectureProgress.objects.get_or_create(
            enrollment_id=enrollment_id,
            lecture=lecture,
        )

        obj.total_sessions = total_sessions
        obj.completed_sessions = completed_sessions
        obj.failed_sessions = failed_sessions
        obj.consecutive_failed_sessions = consecutive_failed
        obj.last_session = progress_qs.last().session if progress_qs.exists() else None
        obj.last_updated = timezone.now()

        obj.save()
        return obj
