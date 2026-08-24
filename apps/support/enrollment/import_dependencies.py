"""Cross-domain dependencies for enrollment import workflows."""

from __future__ import annotations

from typing import Any


class StudentImportDependencyError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class StudentImportIdentityAmbiguousError(StudentImportDependencyError):
    """Enrollment-safe facade error for a non-unique student identity."""


def student_import_valid_school_types(tenant: Any):
    from apps.domains.students.services import student_import_valid_school_types as _valid_types

    return _valid_types(tenant)


def active_student_for_import_identity(
    tenant: Any,
    *,
    ps_number: str = "",
    name: str = "",
    parent_phone: str = "",
    for_update: bool = False,
):
    from apps.domains.students.selectors import (
        AmbiguousStudentImportIdentityError,
        active_student_by_import_identity,
    )

    try:
        return active_student_by_import_identity(
            tenant,
            ps_number=ps_number,
            name=name,
            parent_phone=parent_phone,
            for_update=for_update,
        )
    except AmbiguousStudentImportIdentityError as exc:
        raise StudentImportIdentityAmbiguousError(str(exc)) from exc


def student_import_password_policy(*, password_mode: str | None, initial_password: str | None):
    from apps.domains.students.services import build_student_import_password_policy

    return build_student_import_password_policy(
        password_mode=password_mode,
        initial_password=initial_password,
    )


def resolve_student_import_row(
    tenant: Any,
    row: dict,
    initial_password: str,
    *,
    identity_policy: str,
    valid_school_types,
    source_job_id: str = "",
):
    from apps.domains.students.services import (
        StudentImportRowError,
        resolve_student_import_row as _resolve,
    )

    try:
        return _resolve(
            tenant,
            row,
            initial_password,
            identity_policy=identity_policy,
            valid_school_types=valid_school_types,
            source_job_id=source_job_id,
        )
    except StudentImportRowError as exc:
        raise StudentImportDependencyError(exc.detail) from exc
