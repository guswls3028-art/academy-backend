# PATH: apps/domains/students/services/lecture_enroll.py
"""Compatibility facade for legacy lecture-enrollment student resolution."""

from __future__ import annotations

import logging

from .import_students import StudentImportRowError, resolve_student_import_row

logger = logging.getLogger(__name__)


def get_or_create_student_for_lecture_enroll(tenant, item, password):
    """
    Legacy tuple contract used by older callers/tests.

    New code should call resolve_student_import_row() so student import policy
    stays owned by apps.domains.students.services.import_students.
    """
    try:
        resolved = resolve_student_import_row(
            tenant,
            dict(item) if isinstance(item, dict) else {},
            password,
            identity_policy="phone_if_available",
        )
    except StudentImportRowError as exc:
        logger.debug(
            "[lecture_enroll_compat] student resolve skipped: %s",
            exc.detail,
        )
        return None, False, False
    return resolved.student, resolved.created, resolved.restored
