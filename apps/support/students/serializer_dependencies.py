"""Cross-domain dependencies for student serializers."""

from __future__ import annotations


def get_enrollment_model():
    from apps.domains.enrollment.models import Enrollment

    return Enrollment


def clinic_highlight_map_for_enrollments(*, tenant, enrollment_ids: set[int]) -> dict[int, bool]:
    if not enrollment_ids:
        return {}

    from apps.domains.results.utils.clinic_highlight import compute_clinic_highlight_map

    return compute_clinic_highlight_map(
        tenant=tenant,
        enrollment_ids=enrollment_ids,
    )
