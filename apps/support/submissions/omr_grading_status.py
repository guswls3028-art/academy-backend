"""Read-only grading readiness for tenant-scoped OMR upload progress."""

from django.db.models import F

from apps.domains.results.models import ExamResult, Result
from apps.domains.results.services.omr_subjective_completion import (
    omr_subjective_completion_states,
)


def omr_grading_status_by_submission(
    *, tenant_id: int, submission_ids: set[int],
) -> dict[int, str | None]:
    if not submission_ids:
        return {}
    results = list(Result.objects.filter(
        target_type="exam",
        enrollment__tenant_id=tenant_id,
        attempt__submission_id__in=submission_ids,
    ))
    finalized = set(ExamResult.objects.filter(
        submission_id__in=submission_ids,
        submission__tenant_id=tenant_id,
        submission__target_type="exam",
        exam_id=F("submission__target_id"),
        exam__tenant_id=tenant_id,
        status=ExamResult.Status.FINAL,
    ).values_list("submission_id", flat=True))
    statuses = {}
    for state in omr_subjective_completion_states(results).values():
        if not state.is_omr or not state.scope_valid:
            continue
        if state.manual_review_required:
            status = "manual_review_required"
        elif not state.subjective_complete:
            status = "subjective_pending"
        elif state.submission_id not in finalized:
            status = "grading_pending"
        else:
            status = None
        statuses[int(state.submission_id)] = status
    return statuses
