# apps/domains/progress/tasks/progress_pipeline_task.py
from __future__ import annotations

from celery import shared_task
from django.db import transaction

from apps.domains.submissions.models import Submission
from apps.domains.progress.services.session_calculator import SessionProgressCalculator
from apps.domains.progress.services.lecture_calculator import LectureProgressCalculator
from apps.domains.progress.services.risk_evaluator import RiskEvaluator
from apps.domains.progress.services.clinic_trigger_service import ClinicTriggerService


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=5,
    retry_kwargs={"max_retries": 3},
)
def run_progress_pipeline_task(self, submission_id: int) -> None:
    """
    Results → Progress 파이프라인 (1:N 시험 구조 대응)

    ✅ 핵심 변경:
    - SessionProgressCalculator는 시험 점수를 submission에서 받지 않는다.
    - 시험은 Result 테이블에서 (session에 연결된 모든 exam_id) 기준으로 집계한다.
    """

    submission = Submission.objects.select_related().get(id=submission_id)

    if submission.enrollment_id is None:
        return

    with transaction.atomic():
        # 1) SessionProgress 계산 (시험은 Result 기반 집계)
        session_progress = SessionProgressCalculator.calculate(
            enrollment_id=submission.enrollment_id,
            session=submission.session,
            attendance_type=submission.attendance_type,
            video_progress_rate=submission.video_progress_rate or 0,
            homework_submitted=submission.homework_submitted,
            homework_teacher_approved=submission.homework_teacher_approved,
        )

        # 2) LectureProgress 집계
        lecture_progress = LectureProgressCalculator.calculate(
            enrollment_id=submission.enrollment_id,
            lecture=submission.session.lecture,
        )

        # 3) Risk 평가
        RiskEvaluator.evaluate(lecture_progress)

        # 4) Clinic 자동 트리거 (차시 미완료 기반)
        ClinicTriggerService.auto_create_if_failed(session_progress)

        # (시험 기반 클리닉 추천은 exam_id 단위로 별도 트리거 가능)
        # - 이 메서드는 "특정 exam"을 입력으로 받는 설계이므로,
        #   Session 1:N 상황에서는:
        #     - submission.exam_id가 있을 때만 해당 exam에 대해서 평가
        if getattr(submission, "exam_id", None):
            ClinicTriggerService.auto_create_if_exam_risk(
                enrollment_id=submission.enrollment_id,
                session=submission.session,
                exam_id=submission.exam_id,
            )
