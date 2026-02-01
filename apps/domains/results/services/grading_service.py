# apps/domains/results/services/grading_service.py
from __future__ import annotations

from django.db import transaction
from apps.domains.submissions.models import Submission
from apps.domains.results.services.exam_grading_service import ExamGradingService
from apps.domains.results.models import ExamResult


@transaction.atomic
def grade_submission(submission_id: int) -> ExamResult:
    submission = Submission.objects.select_for_update().get(id=submission_id)

    # answers_ready 아니면 아무것도 안 함 (SSOT)
    if submission.status != Submission.Status.ANSWERS_READY:
        return ExamResult.objects.filter(submission=submission).first()

    service = ExamGradingService()
    output = service.auto_grade_objective(submission_id=submission.id)

    submission.status = Submission.Status.DONE
    submission.save(update_fields=["status", "updated_at"])

    return output.result
