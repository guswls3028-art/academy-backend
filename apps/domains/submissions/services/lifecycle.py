from __future__ import annotations

from typing import Optional

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.transition import (
    InvalidTransitionError,
    bulk_transit,
    can_transit,
    transit,
    transit_save,
)

S = Submission.Status

IN_PROGRESS_STATUSES: tuple[str, ...] = (
    S.SUBMITTED,
    S.DISPATCHED,
    S.EXTRACTING,
    S.ANSWERS_READY,
    S.GRADING,
)

CASCADE_DISCARD_STATUSES: tuple[str, ...] = (
    S.SUBMITTED,
    S.DISPATCHED,
    S.EXTRACTING,
    S.NEEDS_IDENTIFICATION,
    S.ANSWERS_READY,
    S.GRADING,
    S.FAILED,
)

OMR_CONFLICT_STATUSES: tuple[str, ...] = (
    S.SUBMITTED,
    S.DISPATCHED,
    S.EXTRACTING,
    S.NEEDS_IDENTIFICATION,
    S.ANSWERS_READY,
    S.GRADING,
    S.DONE,
)

STUCK_RECOVERABLE_STATUSES: tuple[str, ...] = (
    S.SUBMITTED,
    S.DISPATCHED,
    S.EXTRACTING,
    S.GRADING,
)


def mark_dispatched(
    submission: Submission,
    *,
    actor: str,
    extra_update_fields: Optional[list[str]] = None,
) -> None:
    transit_save(
        submission,
        S.DISPATCHED,
        actor=actor,
        extra_update_fields=extra_update_fields,
    )


def mark_answers_ready(
    submission: Submission,
    *,
    actor: str,
    admin_override: bool = False,
    extra_update_fields: Optional[list[str]] = None,
) -> None:
    transit_save(
        submission,
        S.ANSWERS_READY,
        actor=actor,
        admin_override=admin_override,
        extra_update_fields=extra_update_fields,
    )


def mark_answers_ready_in_memory(
    submission: Submission,
    *,
    actor: str,
    admin_override: bool = False,
) -> None:
    transit(
        submission,
        S.ANSWERS_READY,
        actor=actor,
        admin_override=admin_override,
    )


def mark_needs_identification(
    submission: Submission,
    *,
    actor: str,
    error_message: str = "",
) -> None:
    transit(
        submission,
        S.NEEDS_IDENTIFICATION,
        actor=actor,
        error_message=error_message,
    )


def mark_grading(submission: Submission, *, actor: str) -> None:
    transit_save(submission, S.GRADING, actor=actor)


def mark_done(submission: Submission, *, actor: str) -> None:
    transit_save(submission, S.DONE, actor=actor)


def can_mark_done(status: str) -> bool:
    return can_transit(status, S.DONE)


def can_fail_submission(status: str) -> bool:
    return can_transit(status, S.FAILED)


def fail_submission(
    submission: Submission,
    *,
    error_message: str,
    actor: str,
    admin_override: bool = False,
    extra_update_fields: Optional[list[str]] = None,
) -> None:
    transit_save(
        submission,
        S.FAILED,
        error_message=error_message,
        actor=actor,
        admin_override=admin_override,
        extra_update_fields=extra_update_fields,
    )


def fail_submission_in_memory(
    submission: Submission,
    *,
    error_message: str,
    actor: str,
) -> None:
    transit(submission, S.FAILED, error_message=error_message, actor=actor)


def retry_failed_submission(submission: Submission, *, actor: str) -> None:
    transit_save(submission, S.SUBMITTED, actor=actor)


def reopen_for_regrade(submission: Submission, *, actor: str) -> None:
    mark_answers_ready(submission, actor=actor, admin_override=True)


def reopen_for_regrade_in_memory(submission: Submission, *, actor: str) -> None:
    mark_answers_ready_in_memory(submission, actor=actor, admin_override=True)


def supersede_submission(submission: Submission, *, actor: str) -> None:
    transit_save(submission, S.SUPERSEDED, actor=actor)


def supersede_done_submissions(queryset, *, actor: str = "") -> int:
    # Bulk path is intentionally limited to DONE -> SUPERSEDED and keeps the
    # lower-level guard. Per-row audit belongs in the caller when needed.
    _ = actor
    return bulk_transit(queryset, S.SUPERSEDED, from_status=S.DONE)
