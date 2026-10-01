# PATH: apps/domains/students/services/bulk_from_excel.py
"""Compatibility facade for the student Excel worker import path."""

from __future__ import annotations

from typing import Callable

from .import_students import import_students_from_rows


def bulk_create_students_from_excel_rows(
    *,
    tenant_id: int,
    students_data: list[dict],
    initial_password: str | None = None,
    password_mode: str | None = None,
    parent_initial_password: str | None = None,
    parent_initial_password_mode: str | None = None,
    on_row_progress: Callable[[int, int], None] | None = None,
) -> dict:
    return import_students_from_rows(
        tenant_id=tenant_id,
        students_data=students_data,
        initial_password=initial_password,
        password_mode=password_mode,
        parent_initial_password=parent_initial_password,
        parent_initial_password_mode=parent_initial_password_mode,
        on_row_progress=on_row_progress,
    )
