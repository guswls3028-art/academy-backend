from __future__ import annotations

from typing import Any, Iterable

from django.db.models import Count

from apps.domains.results.models import ExamAttempt


def current_exam_absence_counts(
    *,
    tenant: Any,
    enrollment_ids: Iterable[int],
) -> dict[int, int]:
    """Return cumulative current NOT_SUBMITTED exam counts per enrollment."""
    scoped_ids = {int(enrollment_id) for enrollment_id in enrollment_ids}
    if not scoped_ids:
        return {}

    rows = (
        ExamAttempt.objects.filter(
            enrollment_id__in=scoped_ids,
            enrollment__tenant=tenant,
            exam__tenant=tenant,
            exam__exam_type="regular",
            is_representative=True,
            meta__status="NOT_SUBMITTED",
        )
        .values("enrollment_id")
        .annotate(count=Count("exam_id", distinct=True))
    )
    return {
        int(row["enrollment_id"]): int(row["count"])
        for row in rows
    }
