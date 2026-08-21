"""Public student-activity recording contract."""

from apps.domains.students.services.activity import (
    record_student_login,
    record_student_screen_view,
    record_student_target_open,
)

__all__ = (
    "record_student_login",
    "record_student_screen_view",
    "record_student_target_open",
)
