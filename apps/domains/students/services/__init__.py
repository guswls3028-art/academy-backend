# PATH: apps/domains/students/services/__init__.py
from .school import normalize_school_from_name, get_valid_school_types, get_valid_grades, is_valid_grade, ALL_SCHOOL_TYPES, GRADE_RANGE
from .creation import StudentAccountCreationResult, create_student_account
from .registration_approval import (
    RegistrationApprovalError,
    RegistrationApprovalNotice,
    RegistrationApprovalResult,
    approve_registration_request,
)
from .import_students import (
    StudentImportRowError,
    StudentImportRowResolution,
    import_students_from_rows,
    resolve_student_import_conflicts,
    resolve_student_import_row,
    student_import_valid_school_types,
)
from .lecture_enroll import get_or_create_student_for_lecture_enroll
from .bulk_from_excel import bulk_create_students_from_excel_rows
from .identity import (
    StudentIdentityError,
    derive_student_omr_code,
    normalize_student_phone,
    resolve_student_login_id,
    student_login_id_taken,
)
from .profile import (
    StudentProfileUpdateError,
    derive_omr_code,
    normalize_phone,
    update_student_profile,
)
from .lifecycle import (
    StudentLifecycleError,
    StudentPermanentDeleteResult,
    StudentRestoreResult,
    StudentSoftDeleteResult,
    permanently_delete_students,
    restore_student,
    soft_delete_student,
)

__all__ = [
    "normalize_school_from_name",
    "StudentAccountCreationResult",
    "create_student_account",
    "RegistrationApprovalError",
    "RegistrationApprovalNotice",
    "RegistrationApprovalResult",
    "approve_registration_request",
    "StudentImportRowError",
    "StudentImportRowResolution",
    "import_students_from_rows",
    "resolve_student_import_conflicts",
    "resolve_student_import_row",
    "student_import_valid_school_types",
    "get_or_create_student_for_lecture_enroll",
    "bulk_create_students_from_excel_rows",
    "StudentIdentityError",
    "derive_student_omr_code",
    "normalize_student_phone",
    "resolve_student_login_id",
    "student_login_id_taken",
    "StudentProfileUpdateError",
    "derive_omr_code",
    "normalize_phone",
    "update_student_profile",
    "StudentLifecycleError",
    "StudentPermanentDeleteResult",
    "StudentRestoreResult",
    "StudentSoftDeleteResult",
    "permanently_delete_students",
    "restore_student",
    "soft_delete_student",
]
