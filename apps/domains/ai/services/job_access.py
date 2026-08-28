from __future__ import annotations


def user_can_read_job(*, user, tenant, job_type: str | None) -> bool:
    """Job payloads and progress are staff-only and unknown types fail closed."""
    if not (job_type or "").strip():
        return False
    from apps.core.services.tenant_access import user_has_active_staff_access

    return bool(user_has_active_staff_access(user, tenant))
