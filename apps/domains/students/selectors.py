# PATH: apps/domains/students/selectors.py
"""
Canonical tenant-scoped read entrypoints for the students domain.

This module is intentionally small for Phase 1. It gives touched code a single
place to express tenant/deleted-state intent before broader callers are migrated.
"""

from __future__ import annotations

from typing import Literal

from django.db.models import QuerySet

from apps.domains.students.models import Student

DeletedState = Literal["active", "deleted", "any"]


def _require_tenant(tenant):
    if tenant is None:
        raise ValueError("tenant is required for student selectors")
    return tenant


def students_for_tenant(
    tenant,
    *,
    deleted: DeletedState = "active",
) -> QuerySet[Student]:
    """Return students for one tenant with explicit deleted-state intent."""
    tenant = _require_tenant(tenant)
    qs = Student.objects.filter(tenant=tenant)
    if deleted == "active":
        return qs.filter(deleted_at__isnull=True)
    if deleted == "deleted":
        return qs.filter(deleted_at__isnull=False)
    if deleted == "any":
        return qs
    raise ValueError(f"unknown deleted state: {deleted!r}")


def student_for_tenant_user(tenant, user, *, deleted: DeletedState = "active") -> Student | None:
    if user is None:
        return None
    return students_for_tenant(tenant, deleted=deleted).filter(user=user).first()


def active_students_for_parent(tenant, parent) -> QuerySet[Student]:
    if parent is None:
        return Student.objects.none()
    return students_for_tenant(tenant, deleted="active").filter(parent=parent)


def active_student_by_id(tenant, student_id: int) -> Student | None:
    return students_for_tenant(tenant, deleted="active").filter(id=student_id).first()
