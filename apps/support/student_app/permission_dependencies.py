"""Student selector dependencies for student-app permissions."""

from __future__ import annotations


def student_for_tenant_user(tenant, user, *, deleted: str = "active", for_update: bool = False):
    from apps.domains.students.selectors import student_for_tenant_user as _student_for_tenant_user

    return _student_for_tenant_user(tenant, user, deleted=deleted, for_update=for_update)


def active_students_for_parent(tenant, parent):
    from apps.domains.students.selectors import active_students_for_parent as _active_students_for_parent

    return _active_students_for_parent(tenant, parent)

