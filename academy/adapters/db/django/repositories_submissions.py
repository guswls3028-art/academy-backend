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


def get_submission_tenant_id(submission_id: int) -> int | None:
    from apps.domains.submissions.models import Submission

    return Submission.objects.filter(pk=submission_id).values_list("tenant_id", flat=True).first()


def list_stuck_dispatched_submission_ids(cutoff, *, limit: int = 100) -> list[int]:
    from apps.domains.submissions.models import Submission

    return list(
        Submission.objects.filter(
            status=Submission.Status.DISPATCHED,
            updated_at__lt=cutoff,
        ).values_list("id", flat=True)[:limit]
    )
