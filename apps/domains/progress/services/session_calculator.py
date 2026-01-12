# apps/domains/progress/services/session_calculator.py
from __future__ import annotations

from django.utils import timezone

from apps.domains.progress.models import SessionProgress, ProgressPolicy
from apps.domains.lectures.models import Session


class SessionProgressCalculator:
    """
    차시 단위 진행 계산기

    ⚠️ ProgressPolicy는 반드시 존재해야 한다.
    없으면 여기서 기본값으로 생성한다. (lazy-create)
    """

    @staticmethod
    def _get_or_create_policy(session: Session) -> ProgressPolicy:
        """
        Lecture 단위 ProgressPolicy 보장
        - 정책이 없으면 기본 정책으로 생성
        """
        policy, _ = ProgressPolicy.objects.get_or_create(
            lecture=session.lecture,
            defaults={
                "video_required_rate": 90,
                "exam_start_session_order": 2,
                "exam_end_session_order": 9999,
                "exam_pass_score": 60.0,
                "homework_start_session_order": 2,
                "homework_end_session_order": 9999,
                "homework_pass_type": ProgressPolicy.HomeworkPassType.TEACHER_APPROVAL,
            },
        )
        return policy

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

        # ✅ ProgressPolicy 보장
        policy = SessionProgressCalculator._get_or_create_policy(session)

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
        # Exam (⚠️ 시험은 1:N 가능)
        # ----------------------
        if policy.exam_start_session_order <= session.order <= policy.exam_end_session_order:
            if exam_score is not None:
                obj.exam_score = exam_score
                obj.exam_passed = exam_score >= policy.exam_pass_score
            else:
                # 시험이 있는 주차인데 점수가 아직 없음
                obj.exam_passed = False
        else:
            # 시험 적용 대상 아님
            obj.exam_passed = True

        # ----------------------
        # Homework
        # ----------------------
        if policy.homework_start_session_order <= session.order <= policy.homework_end_session_order:
            obj.homework_submitted = homework_submitted

            if policy.homework_pass_type == ProgressPolicy.HomeworkPassType.SUBMIT:
                obj.homework_passed = homework_submitted

            elif policy.homework_pass_type == ProgressPolicy.HomeworkPassType.SCORE:
                obj.homework_passed = homework_teacher_approved

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
