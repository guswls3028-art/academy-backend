# apps/domains/progress/services/progress_pipeline.py
from __future__ import annotations

from typing import Optional

from django.db import transaction

from apps.domains.submissions.models import Submission
from apps.domains.results.models import Result
from apps.domains.lectures.models import Session

from apps.domains.progress.services.session_calculator import SessionProgressCalculator
from apps.domains.progress.services.lecture_calculator import LectureProgressCalculator
from apps.domains.progress.services.risk_evaluator import RiskEvaluator
from apps.domains.progress.services.clinic_trigger_service import ClinicTriggerService


class ProgressPipeline:
    """
    Results 이후 '학습 진도 파이프라인'

    ⚠️ 주의
    - Results 도메인에서 직접 import 금지
    - 실패해도 재시도 가능해야 함
    - 멱등성 유지
    """

    @staticmethod
    @transaction.atomic
    def run_by_submission(
        *,
        submission: Submission,
        result: Result,
    ) -> None:
        """
        시험 채점 완료 후 Progress 재계산
        """

        if submission.target_type != Submission.TargetType.EXAM:
            return

        # 1️⃣ Exam → Session 매핑
        session: Optional[Session] = (
            Session.objects
            .filter(exam__id=submission.target_id)
            .select_related("lecture")
            .first()
        )

        if not session:
            return

        # 2️⃣ SessionProgress 계산
        sp = SessionProgressCalculator.calculate(
            enrollment_id=submission.enrollment_id,
            session=session,
            attendance_type="online",
            video_progress_rate=100,
            exam_score=result.total_score,
            homework_submitted=True,
            homework_teacher_approved=True,
        )

        # 3️⃣ 클리닉 자동 트리거
        ClinicTriggerService.auto_create_if_failed(sp)

        # 4️⃣ LectureProgress 집계
        lp = LectureProgressCalculator.calculate(
            enrollment_id=submission.enrollment_id,
            lecture=session.lecture,
        )

        # 5️⃣ 위험도 평가
        RiskEvaluator.evaluate(lp)
