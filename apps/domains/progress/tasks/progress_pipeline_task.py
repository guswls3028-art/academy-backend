# apps/domains/progress/tasks/progress_pipeline_task.py
from __future__ import annotations

from celery import shared_task
from django.db import transaction

from apps.domains.submissions.models import Submission
from apps.domains.progress.services.session_calculator import (
    SessionProgressCalculator,
)
from apps.domains.progress.services.lecture_calculator import (
    LectureProgressCalculator,
)
from apps.domains.progress.services.risk_evaluator import RiskEvaluator
from apps.domains.progress.services.clinic_trigger_service import (
    ClinicTriggerService,
)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=5,
    retry_kwargs={"max_retries": 3},
)
def run_progress_pipeline_task(self, submission_id: int) -> None:
    """
    Results → Progress 파이프라인 (MVP)

    흐름:
    1. Submission 조회
    2. SessionProgress 계산
    3. LectureProgress 집계
    4. Risk 평가
    5. Clinic 자동 트리거
    """

    submission = Submission.objects.select_related().get(id=submission_id)

    # 현재는 시험/과제 결과 기반 progress만 처리
    if submission.enrollment_id is None:
        return

    with transaction.atomic():
        # -----------------------------
        # 1️⃣ SessionProgress 계산
        # -----------------------------
        session_progress = SessionProgressCalculator.calculate(
            enrollment_id=submission.enrollment_id,
            session=submission.session,
            attendance_type=submission.attendance_type,
            video_progress_rate=submission.video_progress_rate or 0,
            exam_score=submission.exam_score,
            homework_submitted=submission.homework_submitted,
            homework_teacher_approved=submission.homework_teacher_approved,
        )

        # -----------------------------
        # 2️⃣ LectureProgress 집계
        # -----------------------------
        lecture_progress = LectureProgressCalculator.calculate(
            enrollment_id=submission.enrollment_id,
            lecture=submission.session.lecture,
        )

        # -----------------------------
        # 3️⃣ Risk 평가
        # -----------------------------
        RiskEvaluator.evaluate(lecture_progress)

        # -----------------------------
        # 4️⃣ Clinic 자동 트리거 (기존)
        # -----------------------------
        ClinicTriggerService.auto_create_if_failed(session_progress)

        # =====================================================
        # 🔧 PATCH: 시험 기반 클리닉 자동 추천 (확장)
        #
        # - 기존 파이프라인 흐름 유지
        # - 시험이 없는 submission 은 대상 아님
        # - 합불/위험 판단은 ClinicExamRuleService에 위임
        # =====================================================
        if submission.exam_id:
            ClinicTriggerService.auto_create_if_exam_risk(
                enrollment_id=submission.enrollment_id,
                session=submission.session,
                exam_id=submission.exam_id,
            )
