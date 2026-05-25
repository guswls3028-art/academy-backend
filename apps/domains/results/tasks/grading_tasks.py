# apps/domains/results/tasks/grading_tasks.py
from __future__ import annotations

import logging

from apps.support.submissions.failure import mark_submission_failed

logger = logging.getLogger(__name__)


def _mark_submission_failed(submission_id: int, error_message: str) -> bool:
    return mark_submission_failed(
        int(submission_id),
        error_message=error_message,
        actor="grader.task",
    )


def grade_submission_task(submission_id: int) -> dict:
    """
    채점 작업 실행 함수

    Celery 제거됨: 동기적으로 실행되도록 변경됨.
    필요시 호출부에서 비동기 처리 구현.
    """
    from apps.domains.results.services.grading_service import grade_submission

    try:
        r = grade_submission(int(submission_id))
    except Exception:
        logger.exception(
            "grade_submission_task failed for submission_id=%s", submission_id,
        )
        failed_marked = _mark_submission_failed(
            int(submission_id),
            "grading failed - see logs",
        )
        return {
            "ok": False,
            "submission_id": int(submission_id),
            "error": "grading failed - see logs",
            "failed_marked": failed_marked,
        }

    payload = {
        "ok": True,
        "submission_id": int(submission_id),
        "exam_result_id": int(getattr(r, "id")),
        "total_score": float(getattr(r, "total_score", 0.0) or 0.0),
        "is_passed": bool(getattr(r, "is_passed", False)),
        "status": str(getattr(r, "status", "")),
    }
    logger.info("grade_submission_task done: %s", payload)
    return payload
