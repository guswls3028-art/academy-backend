# PATH: apps/domains/results/services/grading_entrypoint.py
from __future__ import annotations

from apps.domains.submissions.models import Submission
from apps.domains.results.tasks.grading_tasks import grade_submission_task


def enqueue_grading_if_ready(submission: Submission) -> None:
    """
    OMR / ONLINE 공통:
    answers_ready 상태면 grading 큐로 이동
    """
    if submission.status != Submission.Status.ANSWERS_READY:
        return

    grade_submission_task.delay(int(submission.id))
