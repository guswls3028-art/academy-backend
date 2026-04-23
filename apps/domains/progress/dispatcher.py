# apps/domains/progress/dispatcher.py
from __future__ import annotations

from typing import Optional
import logging

from apps.domains.progress.services.progress_pipeline import ProgressPipelineService

logger = logging.getLogger(__name__)


def dispatch_progress_pipeline(
    *,
    exam_id: Optional[int] = None,
    submission_id: Optional[int] = None,
    enrollment_id: Optional[int] = None,
    session_id: Optional[int] = None,
) -> None:
    """
    ✅ Results/Clinic → Progress 진입점 (SSOT, 동기)

    지원 경로:
    - submission_id : 제출(Submission) 하나 기준 재계산
    - exam_id       : 해당 시험을 본 모든 응시자 재계산
    - enrollment_id + session_id : 특정 학생 × 특정 세션 한 점 재계산
      (homework ClinicLink 해소, admin manual resolve 등 exam_id 가 없는 진입점용)

    - Celery/Queue 금지
    - Idempotent service만 호출
    - 실패 시 예외를 올려서 상위 orchestrator가 관측/재시도 정책을 가진다.
    """
    ProgressPipelineService().apply(
        exam_id=exam_id,
        submission_id=submission_id,
        enrollment_id=enrollment_id,
        session_id=session_id,
    )
