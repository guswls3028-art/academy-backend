"""Cross-domain lifecycle dependencies for students."""

from __future__ import annotations

from typing import Any


def ensure_parent_for_student(
    *,
    tenant: Any,
    parent_phone: str,
    student_name: str,
) -> Any | None:
    from apps.domains.parents.services import ensure_parent_for_student as _ensure_parent

    return _ensure_parent(
        tenant=tenant,
        parent_phone=parent_phone,
        student_name=student_name,
    )


def ensure_parent_account_for_student(
    *,
    tenant: Any,
    parent_phone: str,
    student_name: str,
) -> Any:
    from apps.domains.parents.services import ensure_parent_account_for_student as _ensure_parent_account

    return _ensure_parent_account(
        tenant=tenant,
        parent_phone=parent_phone,
        student_name=student_name,
    )


def parent_for_password_reset(*, tenant_id: int, phone: str) -> Any | None:
    from apps.domains.parents.models import Parent

    return Parent.objects.filter(tenant_id=int(tenant_id), phone=phone).first()


def deactivate_enrollments_for_student(*, tenant: Any, student: Any) -> int:
    from apps.domains.enrollment.services.lifecycle import deactivate_enrollments_for_student as _deactivate

    return _deactivate(tenant=tenant, student=student)


def cancel_active_participants_for_student(
    *,
    tenant: Any,
    student: Any,
    changed_at: Any,
) -> int:
    from apps.domains.clinic.services.lifecycle import cancel_active_participants_for_student as _cancel

    return _cancel(tenant=tenant, student=student, changed_at=changed_at)


def update_inventory_student_ps(*, tenant: Any, old_ps: str, new_ps: str) -> None:
    from apps.domains.inventory.models import InventoryFile, InventoryFolder

    InventoryFolder.objects.filter(tenant=tenant, student_ps=old_ps).update(student_ps=new_ps)
    InventoryFile.objects.filter(tenant=tenant, student_ps=old_ps).update(student_ps=new_ps)
