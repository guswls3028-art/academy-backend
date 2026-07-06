"""Cross-domain dependencies for exam views."""

from __future__ import annotations

from apps.domains.results.permissions import IsTeacherOrAdmin


def get_session_model():
    from apps.domains.lectures.models import Session

    return Session


def dispatch_progress_for_exam(*, exam_id: int) -> None:
    from apps.domains.progress.dispatcher import dispatch_progress_pipeline

    dispatch_progress_pipeline(exam_id=exam_id)


def regular_exam_delete_blocker(exam) -> str | None:
    from apps.domains.results.models import Result, ResultFact
    from apps.domains.submissions.models import Submission

    if exam.attempts.exists():
        return "exam attempts"
    if Submission.objects.filter(
        tenant=exam.tenant,
        target_type=Submission.TargetType.EXAM,
        target_id=exam.id,
    ).exists():
        return "submissions"
    if exam.results.exists():
        return "exam results"
    if Result.objects.filter(target_type="exam", target_id=exam.id).exists():
        return "results"
    if ResultFact.objects.filter(target_type="exam", target_id=exam.id).exists():
        return "result facts"
    return None


def resolve_removed_exam_clinic_links(
    *,
    tenant_id: int,
    session_id: int,
    exam_id: int,
    user_id: int | None,
) -> int:
    from apps.domains.progress.dispatcher import resolve_removed_source_clinic_links

    return resolve_removed_source_clinic_links(
        tenant_id=tenant_id,
        session_id=session_id,
        source_type="exam",
        source_id=exam_id,
        user_id=user_id,
        reason="exam_removed_from_session",
    )
