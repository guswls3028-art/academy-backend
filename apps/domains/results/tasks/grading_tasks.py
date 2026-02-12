# apps/domains/results/tasks/grading_tasks.py
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def grade_submission_task(submission_id: int) -> dict:
    """
    채점 작업 실행 함수
    
    Celery 제거됨: 동기적으로 실행되도록 변경됨.
    필요시 호출부에서 비동기 처리 구현.
    """
    from apps.domains.results.services.grading_service import grade_submission

    r = grade_submission(int(submission_id))
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
