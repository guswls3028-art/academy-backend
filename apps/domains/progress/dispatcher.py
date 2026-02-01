# apps/domains/progress/dispatcher.py
from __future__ import annotations

from typing import Optional
import logging

from apps.domains.progress.services.progress_pipeline import ProgressPipelineService

logger = logging.getLogger(__name__)


def dispatch_progress_pipeline(*, exam_id: Optional[int] = None, submission_id: Optional[int] = None) -> None:
    """
    ✅ Results → Progress 진입점 (SSOT, 동기)

    - Celery/Queue 금지
    - Idempotent service만 호출
    - 실패 시 예외를 올려서 상위 orchestrator(results)가 관측/재시도 정책을 가진다.
    """
    ProgressPipelineService().apply(exam_id=exam_id, submission_id=submission_id)
