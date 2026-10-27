"""Public operational boundary; never exposes submission/user payloads."""
from __future__ import annotations


def active_homework_preview_batch_job_ids() -> set[str]:
    """Global queue reconciliation must retain registered private preview jobs.

    This operational query returns provider IDs only. Business endpoints still
    resolve the tenant and authorize the source submission independently.
    """
    from apps.domains.submissions.models import HomeworkMediaPreview

    return set(HomeworkMediaPreview.objects.filter(
        status__in=[HomeworkMediaPreview.Status.QUEUED, HomeworkMediaPreview.Status.PROCESSING],
    ).exclude(batch_job_id="").values_list("batch_job_id", flat=True))
