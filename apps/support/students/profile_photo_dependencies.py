"""Supported cross-domain access to the canonical student photo service."""

from apps.domains.students.services.profile_photo import (
    StudentProfilePhotoStorageError,
    StudentProfilePhotoValidationError,
    replace_student_profile_photo,
)

__all__ = [
    "StudentProfilePhotoStorageError",
    "StudentProfilePhotoValidationError",
    "replace_student_profile_photo",
]
