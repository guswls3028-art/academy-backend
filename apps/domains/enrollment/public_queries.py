"""Public enrollment read helpers for cross-domain callers."""

from __future__ import annotations

from apps.domains.enrollment.models import Enrollment


def get_enrollment_tenant_id(enrollment_id: int) -> int | None:
    """Return the tenant id for an enrollment without exposing the Enrollment model."""
    if not enrollment_id:
        return None
    return (
        Enrollment.objects.filter(id=int(enrollment_id))
        .order_by("id")
        .values_list("tenant_id", flat=True)
        .first()
    )
