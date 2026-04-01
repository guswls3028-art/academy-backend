from __future__ import annotations

from django.db import transaction

from apps.domains.results.models import ExamResult
from apps.domains.results.services.exam_grading_service import ExamGradingService
from apps.domains.results.services.sync_result_from_submission import (
    sync_result_from_exam_submission,
)
from apps.domains.progress.dispatcher import dispatch_progress_pipeline


@transaction.atomic
def grade_submission(submission_id: int) -> ExamResult:
    service = ExamGradingService()
    result = service.auto_grade_objective(submission_id=int(submission_id))

    # ✅ 모든 source(ONLINE, OMR_SCAN 등)에서 Result/ResultItem 동기화 (학생 결과 API용)
    try:
        sync_result_from_exam_submission(submission_id)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Result sync failed for submission %s", submission_id
        )

    # ✅ 시험 채점 완료 → progress / clinic 자동 갱신
    dispatch_progress_pipeline(submission_id=int(submission_id))

    return result
