"""Student selector dependencies for messaging services."""

from __future__ import annotations


def students_for_tenant(tenant, *, deleted: str = "active"):
    from apps.domains.students.selectors import students_for_tenant as _students_for_tenant

    return _students_for_tenant(tenant, deleted=deleted)

