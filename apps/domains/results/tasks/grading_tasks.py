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
    """
    STEP 1 확정:
    - 채점 태스크는 자동 재시도 3회
    - 실제 채점 진입점은 results.services.grader.grade_submission_to_results

    🔧 FIX:
    - 기존 grade_submission 은 존재하지 않음
    - 실제 구현된 함수명으로 정확히 연결
    """
    from apps.domains.submissions.models import Submission
    from apps.domains.results.services.grader import (
        grade_submission_to_results,
    )

    submission = Submission.objects.get(id=int(submission_id))

    logger.info("grading start: submission_id=%s", submission_id)
    grade_submission_to_results(submission)
    logger.info("grading done: submission_id=%s", submission_id)
