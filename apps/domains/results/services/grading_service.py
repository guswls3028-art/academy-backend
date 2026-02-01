# PATH: apps/domains/results/services/grading_service.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from django.db import transaction
from django.db.models import Q

from apps.domains.submissions.models import Submission

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GradeOutcome:
    submission_id: int
    created_attempt_id: Optional[int]
    exam_result_id: Optional[int]
    finalized: bool
    detail: str


def _get_models():
    from apps.domains.results.models.exam_attempt import ExamAttempt
    from apps.domains.results.models.exam_result import ExamResult
    return ExamAttempt, ExamResult


def _get_services():
    from apps.domains.results.services.grading_entrypoint import GradingEntrypoint
    from apps.domains.results.services.attempt_service import AttemptService
    from apps.domains.results.services.applier import ResultApplier
    from apps.domains.progress.tasks.progress_pipeline_task import run_progress_pipeline
    return GradingEntrypoint, AttemptService, ResultApplier, run_progress_pipeline


@transaction.atomic
def grade_submission(submission_id: int) -> object:
    """
    Queue-less grading entry.

    Guarantees:
    - Idempotent: repeated calls won't corrupt results.
    - Converges to a stable representative attempt/result.
    - Does not require any external worker once answers are ready.

    Returns:
      An ExamResult instance (or compatible object) for existing callers.
    """
    ExamAttempt, ExamResult = _get_models()
    GradingEntrypoint, AttemptService, ResultApplier, run_progress_pipeline = _get_services()

    submission = (
        Submission.objects.select_for_update()
        .select_related()
        .get(id=submission_id)
    )

    # ------------------------------------------------------------
    # Guard: only grade when answers exist or this is an ONLINE flow.
    # ------------------------------------------------------------
    if submission.status in (Submission.Status.FAILED,):
        raise RuntimeError("cannot grade FAILED submission")

    # If answers are not ready, do nothing (worker will later submit results).
    # Keep behavior non-destructive.
    if submission.source != Submission.Source.ONLINE and submission.status not in (
        Submission.Status.ANSWERS_READY,
        Submission.Status.GRADING,
        Submission.Status.DONE,
    ):
        # Keep as-is; caller should not crash hard in some flows.
        return _existing_exam_result_or_none(submission_id)

    # ------------------------------------------------------------
    # If already has an ExamResult linked, prefer returning it.
    # ------------------------------------------------------------
    existing = ExamResult.objects.filter(submission_id=submission_id).order_by("-id").first()
    if existing:
        return existing

    # ------------------------------------------------------------
    # Transition submission into GRADING if appropriate.
    # ------------------------------------------------------------
    if submission.status not in (Submission.Status.DONE,):
        submission.status = Submission.Status.GRADING
        submission.error_message = ""
        submission.save(update_fields=["status", "error_message", "updated_at"])

    # ------------------------------------------------------------
    # Create Attempt append-only + apply grading.
    # Existing services in your tree are used; no schema changes.
    # ------------------------------------------------------------
    attempt = AttemptService().create_attempt_for_submission(submission=submission)  # type: ignore

    # GradingEntrypoint performs:
    # - read answers/detected answers from submission/meta
    # - compare with AnswerKey/template resolved
    # - create ResultFact/ResultItems
    # - compute summary and pass/fail
    # - mark representative attempt
    GradingEntrypoint().run(attempt_id=attempt.id)  # type: ignore

    # Apply creates/updates ExamResult row (your results.0003 added this).
    exam_result = ResultApplier().apply_from_attempt(attempt_id=attempt.id)  # type: ignore

    # ------------------------------------------------------------
    # Finalize submission state.
    # ------------------------------------------------------------
    submission.status = Submission.Status.DONE
    submission.save(update_fields=["status", "updated_at"])

    # ------------------------------------------------------------
    # Progress pipeline hook (sync).
    # ------------------------------------------------------------
    try:
        run_progress_pipeline(exam_id=getattr(exam_result, "exam_id", None), submission_id=submission_id)
    except Exception:
        # Do not break grading response; log for ops.
        logger.exception("progress pipeline failed after grading (submission_id=%s)", submission_id)

    return exam_result


def _existing_exam_result_or_none(submission_id: int) -> object:
    ExamAttempt, ExamResult = _get_models()
    r = ExamResult.objects.filter(submission_id=submission_id).order_by("-id").first()
    if r:
        return r
    # Keep compatibility: return a lightweight object rather than None.
    return {"detail": "answers not ready", "submission_id": submission_id}
