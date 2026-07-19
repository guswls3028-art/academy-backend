from __future__ import annotations


STAFF_ONLY_JOB_TYPES = frozenset({
    "problem_studio_package",
    "problem_studio_transfer",
    "problem_studio_transcription",
})


def user_can_read_job(*, user, tenant, job_type: str | None) -> bool:
    if (job_type or "").strip().lower() not in STAFF_ONLY_JOB_TYPES:
        return True
    from apps.core.services.tenant_access import user_has_active_staff_access

    return bool(user_has_active_staff_access(user, tenant))
