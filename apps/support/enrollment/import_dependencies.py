"""Cross-domain dependencies for enrollment import workflows."""

from __future__ import annotations

from typing import Any


class StudentImportDependencyError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def student_import_valid_school_types(tenant: Any):
    from apps.domains.students.services import student_import_valid_school_types as _valid_types

    return _valid_types(tenant)


def resolve_student_import_row(
    tenant: Any,
    row: dict,
    initial_password: str,
    *,
    identity_policy: str,
    valid_school_types,
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
        )
    except StudentImportRowError as exc:
        raise StudentImportDependencyError(exc.detail) from exc
