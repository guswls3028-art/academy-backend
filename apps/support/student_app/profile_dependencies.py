"""Student profile service dependencies for the student app."""

from __future__ import annotations

from apps.domains.students.services import StudentProfileUpdateError, update_student_profile
from apps.domains.students.services.account_notifications import (
    send_parent_account_credentials_notice,
    send_student_account_credentials_notice,
    send_user_password_changed_notice,
)


__all__ = [
    "StudentProfileUpdateError",
    "send_parent_account_credentials_notice",
    "send_student_account_credentials_notice",
    "send_user_password_changed_notice",
    "update_student_profile",
]
