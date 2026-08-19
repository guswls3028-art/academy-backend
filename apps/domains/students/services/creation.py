# PATH: apps/domains/students/services/creation.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from django.db import transaction

from academy.adapters.db.django import repositories_students as student_repo
from apps.core.models import TenantMembership
from apps.support.students.lifecycle_dependencies import ensure_parent_account_for_student

from .account_notice import stage_pending_account_notice


@dataclass(frozen=True)
class StudentAccountCreationResult:
    student: Any
    user: Any
    parent: Any | None
    parent_phone: str
    parent_password_for_notice: str
    parent_user_created: bool

    @property
    def parent_password_by_phone(self) -> dict[str, str]:
        if not self.parent_phone:
            return {}
        return {self.parent_phone: self.parent_password_for_notice}


def create_student_account(
    *,
    tenant,
    student_data: Mapping[str, Any],
    password: str | None = None,
    password_hash: str | None = None,
    must_change_password: bool = False,
    account_notice_student_password: str | None = None,
) -> StudentAccountCreationResult:
    """
    Create the canonical student account graph for one tenant.

    Owns the durable graph and encrypted first-enrollment notice staging:
    Parent ensure -> User -> Student -> TenantMembership(student) -> pending notice.

    Callers keep validation, duplicate/deleted-student policy, API response
    shape, and message dispatch so existing surfaces can migrate safely.
    """
    if password is None and password_hash is None:
        raise ValueError("password or password_hash is required")
    if password is not None and password_hash is not None:
        raise ValueError("password and password_hash are mutually exclusive")

    data = dict(student_data)
    parent_phone = str(data.get("parent_phone") or "").strip()
    name = str(data.get("name") or "").strip()
    ps_number = str(data.get("ps_number") or "").strip()
    if not ps_number:
        raise ValueError("ps_number is required")

    with transaction.atomic():
        parent = None
        parent_password_for_notice = ""
        parent_user_created = False
        if parent_phone:
            parent_result = ensure_parent_account_for_student(
                tenant=tenant,
                parent_phone=parent_phone,
                student_name=name,
            )
            parent = parent_result.parent
            parent_password_for_notice = parent_result.password_for_notice
            parent_user_created = parent_result.user_created

        user = student_repo.user_create_user(
            username=ps_number,
            tenant=tenant,
            phone=data.get("phone") or "",
            name=name,
        )
        if password_hash is not None:
            user.password = password_hash
        else:
            user.set_password(password)
        user.must_change_password = must_change_password
        user.save()

        student = student_repo.student_create(
            tenant=tenant,
            user=user,
            parent=parent,
            **data,
        )

        TenantMembership.ensure_active(
            tenant=tenant,
            user=user,
            role="student",
        )

        notice_student_password = account_notice_student_password or password
        if not notice_student_password:
            raise ValueError(
                "account_notice_student_password is required with password_hash"
            )
        stage_pending_account_notice(
            student=student,
            student_password=notice_student_password,
            parent_password=parent_password_for_notice or "변경되지 않음",
        )

    return StudentAccountCreationResult(
        student=student,
        user=user,
        parent=parent,
        parent_phone=parent_phone,
        parent_password_for_notice=parent_password_for_notice,
        parent_user_created=parent_user_created,
    )
