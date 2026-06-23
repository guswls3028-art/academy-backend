"""Submission-domain DB read helpers for cross-domain callers."""

from __future__ import annotations


def target_type_exam() -> str:
    from apps.domains.submissions.models import Submission

    return Submission.TargetType.EXAM


def target_type_homework() -> str:
    from apps.domains.submissions.models import Submission

    return Submission.TargetType.HOMEWORK


def status_done() -> str:
    from apps.domains.submissions.models import Submission

    return Submission.Status.DONE


def status_failed() -> str:
    from apps.domains.submissions.models import Submission

    return Submission.Status.FAILED


def pending_statuses() -> tuple[str, ...]:
    from apps.domains.submissions.models import Submission

    return (
        Submission.Status.SUBMITTED,
        Submission.Status.DISPATCHED,
        Submission.Status.EXTRACTING,
        Submission.Status.NEEDS_IDENTIFICATION,
        Submission.Status.ANSWERS_READY,
        Submission.Status.GRADING,
    )


def submission_filter_tenant(tenant):
    from apps.domains.submissions.models import Submission

    return Submission.objects.filter(tenant=tenant)
