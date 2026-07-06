"""Student profile service dependencies for the student app."""

from __future__ import annotations

from apps.domains.students.services import StudentProfileUpdateError, update_student_profile


__all__ = ["StudentProfileUpdateError", "update_student_profile"]

