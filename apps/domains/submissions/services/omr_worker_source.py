"""Resolve the immutable source of a claimed OMR job at download time."""
import re

from django.db import transaction
from django.utils import timezone

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.lifecycle import _submission_owned_object_key
from apps.domains.submissions.services.omr_submission_guards import omr_submission_is_archived
from apps.infrastructure.storage.r2 import generate_presigned_get_url
from apps.shared.contracts.ai_job import AIJob
from apps.support.submissions.dependencies import latest_ai_job_for_submission


def _owns_source(scan: Submission) -> bool:
    key = scan.file_key or ""
    if _submission_owned_object_key(tenant_id=scan.tenant_id, key=key, submission_id=scan.pk):
        return True
    # Legacy global keys identify their original submission. A rotated copy
    # can reuse one only while its owner is identifiable in the same exam.
    rotation = (scan.meta or {}).get("rotation_rescan")
    if not isinstance(rotation, dict) or not rotation.get("source_submission_id"):
        return False
    historical = re.fullmatch(r"submissions/([1-9][0-9]{0,18})/.+", key)
    if not historical:
        return False
    return Submission.objects.filter(
        pk=int(historical[1]), tenant_id=scan.tenant_id, target_id=scan.target_id,
        target_type=scan.target_type, source=scan.source, file_key=key,
    ).exists()


@transaction.atomic
def sign_current_omr_source(job: AIJob) -> str:
    payload = job.payload or {}
    if (
        job.type != "omr_grading"
        or job.source_domain != "submissions"
        or not str(job.source_id or "").isdecimal()
        or not str(job.tenant_id or "").isdecimal()
        or str(payload.get("submission_id")) != str(job.source_id)
        or str(payload.get("tenant_id", job.tenant_id)) != str(job.tenant_id)
    ):
        raise ValueError("OMR job source mismatch")
    scan = Submission.objects.select_for_update().filter(
        pk=job.source_id, tenant_id=job.tenant_id,
        target_type=Submission.TargetType.EXAM, source=Submission.Source.OMR_SCAN,
    ).first()
    if (
        scan is None
        or omr_submission_is_archived(scan)
        or isinstance((scan.meta or {}).get("discarded"), dict)
        or scan.status == Submission.Status.SUBMITTED
        or str(payload.get("target_id")) != str(scan.target_id)
        or payload.get("target_type") != scan.target_type
        or not scan.file_key or payload.get("file_key") != scan.file_key
        or not _owns_source(scan)
    ):
        raise ValueError("OMR submission source unavailable")
    current = latest_ai_job_for_submission(submission_id=scan.pk, tenant_id=scan.tenant_id)
    if (
        current is None
        or current.job_id != job.id
        or current.job_type != job.type
        or current.status != "RUNNING"
        or job.claim_locked_at is None
        or current.locked_at != job.claim_locked_at
        or (current.lease_expires_at is not None and current.lease_expires_at <= timezone.now())
    ):
        raise ValueError("OMR worker claim is no longer current")
    return generate_presigned_get_url(key=scan.file_key, expires_in=3600)
