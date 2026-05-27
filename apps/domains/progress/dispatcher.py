# apps/domains/progress/dispatcher.py
from __future__ import annotations

from typing import Iterable, Optional
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


def resolve_removed_source_clinic_links(
    *,
    tenant_id: int,
    session_id: int,
    source_type: str,
    source_id: int,
    enrollment_ids: Optional[Iterable[int]] = None,
    user_id: Optional[int] = None,
    reason: str = "source_removed_from_session",
) -> int:
    """
    Public progress-domain entrypoint for closing ClinicLinks whose source was removed.

    Callers outside the progress domain must use this dispatcher-level API instead
    of importing progress.services internals directly.
    """
    from apps.domains.progress.services.clinic_resolution_service import ClinicResolutionService

    return ClinicResolutionService.resolve_by_removed_source(
        tenant_id=tenant_id,
        session_id=session_id,
        source_type=source_type,
        source_id=source_id,
        enrollment_ids=enrollment_ids,
        user_id=user_id,
        reason=reason,
    )
