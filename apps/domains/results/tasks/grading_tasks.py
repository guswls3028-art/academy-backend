# apps/domains/results/tasks/grading_tasks.py
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from celery import shared_task  # type: ignore
except Exception:  # pragma: no cover
    # Celery 미사용 환경에서도 import error로 서버가 죽으면 안 된다.
    def shared_task(*dargs, **dkwargs):  # type: ignore
        def _decorator(fn):
            return fn
        return _decorator


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 5})
def grade_submission_task(self, submission_id: int) -> dict:
    """
    AI callbacks → 채점 enqueue 용.
    - 재시도는 Celery 레벨에서 처리(운영 정석).
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
