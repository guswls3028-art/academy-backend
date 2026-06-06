# PATH: apps/domains/students/services/import_students.py
"""Canonical student import row orchestration.

Excel/student import and lecture-enrollment import both need the same student
identity policy: resolve by tenant + name + parent phone, restore matching
deleted students, or create the account graph through create_student_account().
HTTP views and workers keep their own transport contracts; this module owns the
row-level student decision.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal

from django.db import transaction

from academy.adapters.db.django import repositories_core as core_repo
from academy.adapters.db.django import repositories_enrollment as enroll_repo
from academy.adapters.db.django import repositories_students as student_repo

from .creation import create_student_account
from .identity import (
    StudentIdentityError,
    derive_student_omr_code,
    normalize_student_phone,
    phone_digits,
    resolve_student_login_id,
)
from .lifecycle import permanently_delete_students, restore_student
from .school import get_valid_school_types, is_valid_grade, normalize_school_from_name

logger = logging.getLogger(__name__)

StudentImportIdentityPolicy = Literal["phone_if_available", "random"]


class StudentImportRowError(ValueError):
    def __init__(self, detail: str, *, conflict_student_id: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.conflict_student_id = conflict_student_id


@dataclass(frozen=True)
class StudentImportRowResolution:
    student: Any
    created: bool
    restored: bool
    duplicate: bool
    parent_phone: str
    parent_password_for_notice: str


@dataclass(frozen=True)
class _NormalizedImportRow:
    name: str
    parent_phone: str
    phone: str | None
    student_data: dict[str, Any]
    restore_data: dict[str, Any]


def _digits(value: Any) -> str:
    return phone_digits(value)


def _valid_student_phone(value: Any) -> str | None:
    if not str(value or "").strip():
        return None
    try:
        return normalize_student_phone(
            value,
            required=False,
            field_name="phone",
            field_label="학생 전화번호",
        )
    except StudentIdentityError as exc:
        detail = exc.detail.get("phone", str(exc.detail)) if isinstance(exc.detail, dict) else exc.detail
        raise StudentImportRowError(detail) from exc


def _valid_parent_phone(value: Any) -> str:
    try:
        phone = normalize_student_phone(
            value,
            required=True,
            field_name="parent_phone",
            field_label="학부모 전화번호",
        )
    except StudentIdentityError as exc:
        detail = exc.detail.get("parent_phone", str(exc.detail)) if isinstance(exc.detail, dict) else exc.detail
        raise StudentImportRowError(detail) from exc
    if not phone:
        raise StudentImportRowError("학부모 전화번호는 010XXXXXXXX 11자리여야 합니다.")
    return phone


def _grade_value(value: Any, school_type: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        grade = int(value)
    except (TypeError, ValueError) as exc:
        raise StudentImportRowError("학년은 숫자여야 합니다.") from exc
    if not is_valid_grade(school_type, grade):
        raise StudentImportRowError(f"{school_type} 학생의 학년이 허용 범위를 벗어났습니다.")
    return grade


def student_import_valid_school_types(tenant) -> frozenset[str]:
    program = core_repo.program_get_by_tenant_only_feature_flags(tenant)
    school_level_mode = (
        program.feature_flags.get("school_level_mode")
        if program and program.feature_flags
        else None
    )
    return frozenset(get_valid_school_types(school_level_mode))


def _validate_school_level(
    *,
    valid_school_types: frozenset[str],
    school_type: str,
    grade: int | None,
) -> None:
    if school_type not in valid_school_types:
        labels = {"ELEMENTARY": "초등", "MIDDLE": "중등", "HIGH": "고등"}
        allowed = ", ".join(labels.get(t, t) for t in sorted(valid_school_types))
        raise StudentImportRowError(f"이 학원에서는 {allowed} 학생만 등록할 수 있습니다.")
    if grade is not None and not is_valid_grade(school_type, grade):
        raise StudentImportRowError("허용되지 않는 학년입니다.")


def _normalize_import_row(
    *,
    raw: dict[str, Any],
    valid_school_types: frozenset[str],
) -> _NormalizedImportRow:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise StudentImportRowError("학생 이름은 필수입니다.")

    parent_phone = _valid_parent_phone(raw.get("parent_phone") or raw.get("parentPhone"))
    phone = _valid_student_phone(raw.get("phone") or raw.get("studentPhone"))

    school_val = str(raw.get("school") or "").strip() or None
    school_type, elementary_school, high_school, middle_school = normalize_school_from_name(
        school_val,
        raw.get("school_type"),
    )
    grade = _grade_value(raw.get("grade"), school_type)
    _validate_school_level(
        valid_school_types=valid_school_types,
        school_type=school_type,
        grade=grade,
    )

    high_school_class = (
        str(raw.get("high_school_class") or raw.get("schoolClass") or "").strip() or None
        if school_type == "HIGH"
        else None
    )
    major = (
        str(raw.get("major") or "").strip() or None
        if school_type == "HIGH"
        else None
    )
    gender = str(raw.get("gender") or "").strip().upper()[:1] or None
    if gender not in ("M", "F", None):
        gender = None

    base_profile = {
        "name": name,
        "phone": phone,
        "parent_phone": parent_phone,
        "uses_identifier": bool(raw.get("uses_identifier", False)) or not bool(phone),
        "gender": gender,
        "school_type": school_type,
        "elementary_school": elementary_school,
        "high_school": high_school,
        "middle_school": middle_school,
        "high_school_class": high_school_class,
        "major": major,
        "grade": grade,
        "memo": str(raw.get("memo") or "").strip() or None,
        "is_managed": raw.get("is_managed", True),
    }
    restore_data = {
        **base_profile,
        "school": school_val,
        "schoolClass": high_school_class,
    }
    return _NormalizedImportRow(
        name=name,
        parent_phone=parent_phone,
        phone=phone,
        student_data=base_profile,
        restore_data=restore_data,
    )


def _choose_ps_number(
    *,
    tenant,
    phone: str | None,
    identity_policy: StudentImportIdentityPolicy,
) -> str:
    try:
        return resolve_student_login_id(
            tenant=tenant,
            requested_id="",
            phone=phone if identity_policy == "phone_if_available" else "",
        )
    except StudentIdentityError as exc:
        detail = exc.detail.get("ps_number", str(exc.detail)) if isinstance(exc.detail, dict) else exc.detail
        raise StudentImportRowError(detail) from exc


def resolve_student_import_row(
    tenant,
    row: dict[str, Any],
    initial_password: str,
    *,
    identity_policy: StudentImportIdentityPolicy = "phone_if_available",
    valid_school_types: frozenset[str] | None = None,
) -> StudentImportRowResolution:
    """Resolve one imported row to an active student in one tenant."""
    initial_password = (initial_password or "").strip()
    if len(initial_password) < 4:
        raise ValueError("initial_password는 4자 이상이어야 합니다.")

    normalized = _normalize_import_row(
        raw=row,
        valid_school_types=valid_school_types or student_import_valid_school_types(tenant),
    )

    existing = student_repo.student_filter_tenant_name_parent_phone_active(
        tenant,
        normalized.name,
        normalized.parent_phone,
    )
    if existing:
        return StudentImportRowResolution(
            student=existing,
            created=False,
            restored=False,
            duplicate=True,
            parent_phone=normalized.parent_phone,
            parent_password_for_notice="",
        )

    deleted_student = student_repo.student_filter_tenant_name_parent_phone_deleted(
        tenant,
        normalized.name,
        normalized.parent_phone,
    )
    if deleted_student:
        restored_result = restore_student(
            deleted_student,
            tenant=tenant,
            profile_data=normalized.restore_data,
        )
        return StudentImportRowResolution(
            student=restored_result.student,
            created=False,
            restored=True,
            duplicate=False,
            parent_phone=normalized.parent_phone,
            parent_password_for_notice="",
        )

    ps_number = _choose_ps_number(
        tenant=tenant,
        phone=normalized.phone,
        identity_policy=identity_policy,
    )
    try:
        omr_code = derive_student_omr_code(
            phone=normalized.phone,
            parent_phone=normalized.parent_phone,
        )
    except StudentIdentityError as exc:
        detail = exc.detail.get("omr_code", str(exc.detail)) if isinstance(exc.detail, dict) else exc.detail
        raise StudentImportRowError(detail) from exc
    student_data = {
        **normalized.student_data,
        "ps_number": ps_number,
        "omr_code": omr_code,
    }

    with transaction.atomic():
        if normalized.phone:
            conflict_deleted = (
                student_repo.student_filter_tenant_phone_deleted(
                    tenant,
                    normalized.phone,
                )
                .values_list("id", flat=True)
                .first()
            )
            if conflict_deleted:
                raise StudentImportRowError(
                    "삭제된 학생과 전화번호 충돌. 복원 또는 삭제 후 재등록을 선택하세요.",
                    conflict_student_id=conflict_deleted,
                )
            if student_repo.user_filter_phone_active(normalized.phone, tenant=tenant).exists():
                raise StudentImportRowError("이미 사용 중인 전화번호입니다.")
        if student_repo.student_filter_tenant_ps_number(tenant, ps_number).exists():
            raise StudentImportRowError("이미 사용 중인 PS 번호입니다.")

        created = create_student_account(
            tenant=tenant,
            password=initial_password,
            student_data=student_data,
        )

    created.student._parent_password_for_notice = created.parent_password_for_notice
    return StudentImportRowResolution(
        student=created.student,
        created=True,
        restored=False,
        duplicate=False,
        parent_phone=created.parent_phone,
        parent_password_for_notice=created.parent_password_for_notice,
    )


def import_students_from_rows(
    *,
    tenant_id: int,
    students_data: list[dict],
    initial_password: str,
    send_welcome_message: bool = True,
    on_row_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """
    Import parsed student rows without lecture enrollment.

    Returns the existing worker contract:
    {
      "created": int,
      "failed": [{"row", "name", "error", "conflict_student_id"?}],
      "duplicates": [{"row", "name", "student_id"}],
      "restored": [{"row", "name", "student_id"}],
      "total": int,
      "processed_by": "worker",
    }
    """
    tenant = enroll_repo.get_tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("tenant_id not found")

    initial_password = (initial_password or "").strip()
    if len(initial_password) < 4:
        raise ValueError("initial_password는 4자 이상이어야 합니다.")

    created_students: list[Any] = []
    failed: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    restored: list[dict[str, Any]] = []
    parent_password_by_phone: dict[str, str] = {}
    total = len(students_data)
    skipped_empty = 0
    valid_school_types = student_import_valid_school_types(tenant)

    for row_index, raw in enumerate(students_data, start=1):
        if on_row_progress and total > 0:
            on_row_progress(row_index, total)

        row = dict(raw) if isinstance(raw, dict) else {}
        raw_name = str(row.get("name") or "").strip()
        display_name = raw_name or "(이름 없음)"
        parent_phone = _digits(row.get("parent_phone") or row.get("parentPhone"))
        if not raw_name and not parent_phone:
            skipped_empty += 1
            continue

        try:
            resolved = resolve_student_import_row(
                tenant,
                row,
                initial_password,
                identity_policy="phone_if_available",
                valid_school_types=valid_school_types,
            )
        except StudentImportRowError as exc:
            failed.append({
                "row": row_index,
                "name": display_name,
                "error": exc.detail,
                "conflict_student_id": exc.conflict_student_id,
            })
            continue
        except Exception as exc:
            logger.warning(
                "import_students_from_rows row=%s name=%r: %s",
                row_index,
                display_name,
                exc,
                exc_info=True,
            )
            failed.append({
                "row": row_index,
                "name": display_name,
                "error": str(exc)[:500],
                "conflict_student_id": None,
            })
            continue

        if resolved.created:
            created_students.append(resolved.student)
            if resolved.parent_phone:
                parent_password_by_phone[resolved.parent_phone] = (
                    resolved.parent_password_for_notice
                    or getattr(resolved.student, "_parent_password_for_notice", "변경되지 않음")
                )
        elif resolved.restored:
            restored.append({
                "row": row_index,
                "name": display_name,
                "student_id": resolved.student.id,
            })
        elif resolved.duplicate:
            duplicates.append({
                "row": row_index,
                "name": display_name,
                "student_id": resolved.student.id,
            })

    if send_welcome_message and created_students:
        try:
            from apps.domains.messaging.services import get_tenant_site_url, send_welcome_messages

            send_welcome_messages(
                created_students=created_students,
                student_password=initial_password,
                parent_password_by_phone=parent_password_by_phone,
                site_url=get_tenant_site_url(tenant),
            )
        except Exception:
            logger.exception("student_import: send_welcome_messages failed (non-fatal)")

    if not created_students and not failed and not duplicates and not restored and total > 0:
        logger.error(
            "[student_import] ALL students skipped: total=%s skipped_empty=%s",
            total,
            skipped_empty,
        )
        raise ValueError(
            f"등록할 수 있는 학생이 없습니다. "
            f"전체 {total}행 중 {skipped_empty}행이 이름·전화 모두 비어 건너뜀. "
            f"이름·학부모 전화번호(010 11자리)를 확인해 주세요."
        )

    return {
        "created": len(created_students),
        "failed": failed,
        "duplicates": duplicates,
        "restored": restored,
        "total": total,
        "processed_by": "worker",
    }


def resolve_student_import_conflicts(
    *,
    tenant,
    resolutions: list[dict],
    initial_password: str,
    send_welcome_message: bool = False,
) -> dict:
    """
    Resolve deleted-student import conflicts through the same row policy.

    Existing HTTP contract:
    {
      "created": int,
      "restored": int,
      "failed": [{"row", "name", "error", "conflict_student_id"?}],
    }
    """
    initial_password = (initial_password or "").strip()
    if len(initial_password) < 4:
        raise ValueError("initial_password는 4자 이상이어야 합니다.")
    if not isinstance(resolutions, (list, tuple)):
        raise ValueError("resolutions는 배열이어야 합니다.")

    created_count = 0
    restored_count = 0
    failed: list[dict[str, Any]] = []
    created_students: list[Any] = []
    parent_password_by_phone: dict[str, str] = {}
    valid_school_types = student_import_valid_school_types(tenant)

    for resolution in resolutions:
        item = resolution if isinstance(resolution, dict) else {}
        row = item.get("row")
        student_id = item.get("student_id")
        action = item.get("action")
        student_data = item.get("student_data") or {}
        if not isinstance(student_data, dict):
            student_data = {}
        display_name = str(student_data.get("name") or "").strip()

        if not student_id or action not in ("restore", "delete"):
            failed.append({
                "row": row,
                "name": display_name,
                "error": "잘못된 resolution",
                "conflict_student_id": None,
            })
            continue

        try:
            deleted_student = student_repo.student_filter_tenant_id_deleted_first(
                tenant,
                student_id,
            )
            if not deleted_student:
                failed.append({
                    "row": row,
                    "name": display_name,
                    "error": "삭제된 학생을 찾을 수 없습니다.",
                    "conflict_student_id": None,
                })
                continue

            if action == "restore":
                restore_student(
                    deleted_student,
                    tenant=tenant,
                    profile_data=student_data,
                )
                restored_count += 1
                continue

            with transaction.atomic():
                permanently_delete_students(
                    tenant=tenant,
                    student_ids=[deleted_student.id],
                )
                resolved = resolve_student_import_row(
                    tenant,
                    student_data,
                    initial_password,
                    identity_policy="phone_if_available",
                    valid_school_types=valid_school_types,
                )
            if resolved.created:
                created_count += 1
                created_students.append(resolved.student)
                if resolved.parent_phone:
                    parent_password_by_phone[resolved.parent_phone] = (
                        resolved.parent_password_for_notice
                        or getattr(resolved.student, "_parent_password_for_notice", "변경되지 않음")
                    )
            elif resolved.restored:
                restored_count += 1
            elif resolved.duplicate:
                failed.append({
                    "row": row,
                    "name": display_name,
                    "error": "이미 있는 학생입니다.",
                    "conflict_student_id": getattr(resolved.student, "id", None),
                })
        except StudentImportRowError as exc:
            failed.append({
                "row": row,
                "name": display_name,
                "error": exc.detail,
                "conflict_student_id": exc.conflict_student_id,
            })
        except Exception as exc:
            logger.warning(
                "resolve_student_import_conflicts row=%s name=%r: %s",
                row,
                display_name,
                exc,
                exc_info=True,
            )
            failed.append({
                "row": row,
                "name": display_name,
                "error": str(exc)[:500],
                "conflict_student_id": None,
            })

    if send_welcome_message and created_students:
        try:
            from apps.domains.messaging.services import get_tenant_site_url, send_welcome_messages

            send_welcome_messages(
                created_students=created_students,
                student_password=initial_password,
                parent_password_by_phone=parent_password_by_phone,
                site_url=get_tenant_site_url(tenant),
            )
        except Exception:
            logger.exception("student_import_conflicts: send_welcome_messages failed (non-fatal)")

    return {
        "created": created_count,
        "restored": restored_count,
        "failed": failed,
    }
