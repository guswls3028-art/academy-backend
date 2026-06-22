from __future__ import annotations

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services import lifecycle


S = Submission.Status


def _make_submission(status: str) -> Submission:
    submission = Submission.__new__(Submission)
    submission.pk = 101
    submission.id = 101
    submission.status = status
    submission.error_message = ""
    submission._saved_fields = []

    def _save(update_fields=None):
        submission._saved_fields = list(update_fields or [])

    submission.save = _save
    return submission


def test_lifecycle_status_cohorts_are_intentional():
    assert lifecycle.IN_PROGRESS_STATUSES == (
        S.SUBMITTED,
        S.DISPATCHED,
        S.EXTRACTING,
        S.ANSWERS_READY,
        S.GRADING,
    )
    assert S.FAILED in lifecycle.CASCADE_DISCARD_STATUSES
    assert S.DONE in lifecycle.OMR_CONFLICT_STATUSES
    assert S.SUPERSEDED not in lifecycle.OMR_CONFLICT_STATUSES
    assert lifecycle.STUCK_RECOVERABLE_STATUSES == (
        S.SUBMITTED,
        S.DISPATCHED,
        S.EXTRACTING,
        S.GRADING,
    )


def test_lifecycle_public_methods_hide_raw_status_writes():
    submission = _make_submission(S.DISPATCHED)

    lifecycle.mark_answers_ready(submission, actor="test.lifecycle")

    assert submission.status == S.ANSWERS_READY
    assert set(submission._saved_fields) >= {"status", "error_message", "updated_at"}


def test_lifecycle_reopen_for_regrade_uses_named_override():
    submission = _make_submission(S.DONE)

    lifecycle.reopen_for_regrade(submission, actor="test.regrade")

    assert submission.status == S.ANSWERS_READY


def test_lifecycle_fail_helper_clears_to_failed_with_message():
    submission = _make_submission(S.EXTRACTING)

    lifecycle.fail_submission(
        submission,
        error_message="stuck:extracting_timeout",
        actor="test.recovery",
    )

    assert submission.status == S.FAILED
    assert submission.error_message == "stuck:extracting_timeout"
