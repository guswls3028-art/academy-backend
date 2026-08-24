from __future__ import annotations


SELF_REGISTRATION_DISABLED_TENANT_CODES = frozenset({"godmin", "tchul"})


def is_student_self_registration_enabled(tenant) -> bool:
    """Return the tenant's product policy for public student self-registration."""
    code = str(getattr(tenant, "code", "") or "").strip().lower()
    return bool(code) and code not in SELF_REGISTRATION_DISABLED_TENANT_CODES
