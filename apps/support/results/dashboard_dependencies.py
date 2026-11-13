"""Cross-domain dashboard dependencies for result endpoints."""

from __future__ import annotations

from typing import Any


def pending_work_counts(*, tenant: Any) -> dict[str, int]:
    from academy.adapters.db.django import repositories_submissions as submissions_repo
    from apps.domains.community.selectors.post_selector import get_pending_post_counts

    counts = get_pending_post_counts(tenant)
    counts["submission_pending"] = submissions_repo.exam_inbox_submissions(tenant).filter(
        status__in=submissions_repo.pending_statuses(),
    ).count()
    return counts


def failed_video_count_since(*, tenant: Any, cutoff: Any) -> int:
    from apps.domains.video.models import Video

    return Video.objects.filter(
        tenant=tenant,
        status=Video.Status.FAILED,
        deleted_at__isnull=True,
        updated_at__gte=cutoff,
    ).count()

