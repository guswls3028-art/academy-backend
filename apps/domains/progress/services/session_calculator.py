# apps/domains/progress/services/session_calculator.py
from __future__ import annotations

from django.utils import timezone

from apps.domains.progress.models import SessionProgress, ProgressPolicy
from apps.domains.lectures.models import Session


class SessionProgressCalculator:
    """
    차시 단위 진행 계산기
    """

    @staticmethod
    def calculate(
        *,
        enrollment_id: int,
        session: Session,
        attendance_type: str,
        video_progress_rate: int = 0,
        exam_score: float | None = None,
        homework_submitted: bool = False,
        homework_teacher_approved: bool = False,
    ) -> SessionProgress:
        """
        외부 도메인 값들을 받아서
        SessionProgress를 계산/업데이트
        """

        policy = ProgressPolicy.objects.get(lecture=session.lecture)

        obj, _ = SessionProgress.objects.get_or_create(
            enrollment_id=enrollment_id,
            session=session,
        )

        # ----------------------
        # Attendance / Video
        # ----------------------
        obj.attendance_type = attendance_type
        obj.video_progress_rate = video_progress_rate

        if attendance_type == SessionProgress.AttendanceType.OFFLINE:
            obj.video_completed = True
        else:
            obj.video_completed = video_progress_rate >= policy.video_required_rate

        # ----------------------
        # Exam
        # ----------------------
        if policy.exam_start_session_order <= session.order <= policy.exam_end_session_order:
            if exam_score is not None:
                obj.exam_score = exam_score
                obj.exam_passed = exam_score >= policy.exam_pass_score
        else:
            obj.exam_passed = True  # 적용 대상 아님 → 통과 처리

        # ----------------------
        # Homework
        # ----------------------
        if policy.homework_start_session_order <= session.order <= policy.homework_end_session_order:
            obj.homework_submitted = homework_submitted

            if policy.homework_pass_type == ProgressPolicy.HomeworkPassType.SUBMIT:
                obj.homework_passed = homework_submitted

            elif policy.homework_pass_type == ProgressPolicy.HomeworkPassType.SCORE:
                obj.homework_passed = homework_teacher_approved  # 점수판정은 외부에서 계산 후 true/false 전달

            elif policy.homework_pass_type == ProgressPolicy.HomeworkPassType.TEACHER_APPROVAL:
                obj.homework_passed = homework_teacher_approved
        else:
            obj.homework_passed = True

        # ----------------------
        # Final Completion
        # ----------------------
        obj.completed = (
            obj.video_completed
            and obj.exam_passed
            and obj.homework_passed
        )

        if obj.completed and not obj.completed_at:
            obj.completed_at = timezone.now()

        obj.calculated_at = timezone.now()
        obj.save()

        return obj
