from __future__ import annotations

from django.db.models import Exists, OuterRef

from apps.domains.submissions.models import OmrUploadBatchItem, Submission
from apps.domains.submissions.services.lifecycle import OMR_CONFLICT_STATUSES
from apps.support.omr.candidate_matching import (
    ensure_exam_enrollment_candidate,
    lock_exam_enrollment_candidate,
)


def omr_submission_is_archived(submission: Submission) -> bool:
    meta = submission.meta if isinstance(submission.meta, dict) else {}
    return submission.status == Submission.Status.SUPERSEDED or (
        submission.status == Submission.Status.FAILED
        and (
            str(submission.error_message or "").startswith("discarded:")
            or isinstance(meta.get("discarded"), dict)
        )
    )


def allow_duplicate_requested(request) -> bool:
    # HTTP-only validation must not become an import-time dependency of the
    # AI worker callback, whose intentionally minimal image does not ship DRF.
    from apps.api.common.query_params import parse_query_bool

    return bool(
        parse_query_bool(
            request.query_params,
            "allow_duplicate",
            default=False,
        )
    )


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


def omr_admissions_in_other_sessions(*, tenant, exam_id: int, session_id: int, submission_id: int | OuterRef):
    # The one-to-one submission link is the original admission. Duplicate
    # items only reference it through duplicate_of_submission and never change its origin.
    return OmrUploadBatchItem.objects.filter(
        submission_id=submission_id, batch__tenant=tenant, batch__exam_id=exam_id,
        batch__session_id__isnull=False,
    ).exclude(batch__session_id=session_id)


def omr_replacement_crosses_sessions(*, tenant, exam_id: int, session_id: int, enrollment_id: int, submission_id: int) -> bool:
    return Submission.objects.filter(
        Exists(omr_admissions_in_other_sessions(
            tenant=tenant, exam_id=exam_id, session_id=session_id, submission_id=OuterRef("pk"),
        )),
        tenant=tenant, target_type=Submission.TargetType.EXAM, target_id=exam_id,
        enrollment_id=enrollment_id, source=Submission.Source.OMR_SCAN,
        status__in=OMR_CONFLICT_STATUSES,
    ).exclude(id=submission_id).exists()
