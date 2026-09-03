"""Canonical role capabilities for the clinic workbench."""

from apps.core.services.tenant_access import STAFF_ROLES, get_authorized_tenant_role


def clinic_capabilities_for(user, tenant) -> dict[str, dict[str, bool]]:
    role = get_authorized_tenant_role(user, tenant)
    is_staff = role in STAFF_ROLES
    return {
        "student_operations": {"read": is_staff, "write": is_staff},
        "student_contacts": {"read": is_staff, "write": False},
        "booking_policy": {
            "read": is_staff,
            "write": role in {"owner", "admin"},
        },
    }
