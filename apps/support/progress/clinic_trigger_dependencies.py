"""Cross-domain dependencies for clinic trigger services."""

from __future__ import annotations


def get_enrollment_tenant_id(enrollment_id: int) -> int | None:
    from apps.domains.enrollment.public_queries import (
        get_enrollment_tenant_id as _get_enrollment_tenant_id,
    )

    return _get_enrollment_tenant_id(int(enrollment_id))

