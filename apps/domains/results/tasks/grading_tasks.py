# apps/domains/results/tasks/grading_tasks.py
from __future__ import annotations

import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3},
    retry_backoff=True,
    retry_jitter=True,
)
def grade_submission_task(self, submission_id: int) -> None:

    from apps.domains.submissions.models import Submission
    from apps.domains.results.services.grader import (
        grade_submission_to_results,
    )

    submission = Submission.objects.get(id=int(submission_id))

    # ✅ PATCH: 상태 가드
    try:
        answers_ready = (submission.status == Submission.Status.ANSWERS_READY)
    except Exception:
        answers_ready = True

    if not answers_ready:
        logger.info(
            "grading skipped (not answers_ready): submission_id=%s status=%s",
            submission_id,
            getattr(submission, "status", None),
        )
        return

    logger.info("grading start: submission_id=%s", submission_id)
    grade_submission_to_results(submission)
    logger.info("grading done: submission_id=%s", submission_id)
