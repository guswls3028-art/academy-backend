from __future__ import annotations

from apps.domains.submissions.models import Submission
from apps.support.omr.candidate_matching import (
    ensure_exam_enrollment_candidate,
    lock_exam_enrollment_candidate,
)


OMR_CONFLICT_STATUSES = (
    Submission.Status.SUBMITTED,
    Submission.Status.DISPATCHED,
    Submission.Status.EXTRACTING,
    Submission.Status.NEEDS_IDENTIFICATION,
    Submission.Status.ANSWERS_READY,
    Submission.Status.GRADING,
    Submission.Status.DONE,
)


def allow_duplicate_requested(request) -> bool:
    raw = str(request.query_params.get("allow_duplicate") or "").lower()
    return raw in ("1", "true", "yes")


def find_conflicting_exam_submission(
    *,
    tenant,
    exam_id: int,
    enrollment_id: int | None,
    exclude_submission_id: int | None = None,
):
    if not enrollment_id:
        return None
    qs = Submission.objects.filter(
        tenant=tenant,
        target_type=Submission.TargetType.EXAM,
        target_id=int(exam_id),
        enrollment_id=int(enrollment_id),
        status__in=OMR_CONFLICT_STATUSES,
    )
    if exclude_submission_id is not None:
        qs = qs.exclude(id=int(exclude_submission_id))
    return qs.order_by("-id").first()


def duplicate_conflict_payload(submission: Submission) -> dict:
    return {
        "detail": "이미 이 학생의 답안지가 있습니다. 덮어쓰려면 확인이 필요합니다.",
        "code": "DUPLICATE_ENROLLMENT",
        "conflict_submission_id": int(submission.id),
        "conflict_file_key": submission.file_key or "",
        "conflict_status": submission.status,
    }
