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


class AmbiguousStudentImportIdentityError(LookupError):
    """Raised when an import identity does not select one student safely."""


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


def active_student_by_import_identity(
    tenant,
    *,
    ps_number: str = "",
    name: str = "",
    parent_phone: str = "",
    for_update: bool = False,
) -> Student | None:
    """Prefer exact student ID, else require one normalized name/parent match."""
    candidates = students_for_tenant(tenant, deleted="active").order_by("id")
    ps_number = str(ps_number or "").strip()
    if ps_number:
        candidates = candidates.filter(ps_number=ps_number)
        if for_update:
            candidates = candidates.select_for_update()
        matches = list(candidates[:2])
    else:
        normalized_parent_phone = "".join(
            character for character in str(parent_phone or "") if character.isdigit()
        )
        candidates = candidates.filter(name=name)
        if for_update:
            candidates = candidates.select_for_update()
        matches = []
        for candidate in candidates.iterator(chunk_size=100):
            candidate_parent_phone = "".join(
                character
                for character in str(candidate.parent_phone or "")
                if character.isdigit()
            )
            if candidate_parent_phone != normalized_parent_phone:
                continue
            matches.append(candidate)
            if len(matches) == 2:
                break
    if len(matches) > 1:
        raise AmbiguousStudentImportIdentityError(
            "동일한 학생 식별값을 가진 활성 학생이 2명 이상입니다."
        )
    return matches[0] if matches else None
