from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.models import TenantMembership
from apps.domains.parents.services import ensure_parent_for_student
from apps.domains.students.models import Student
from apps.domains.students.services.school import is_valid_grade, normalize_school_from_name


class StudentLifecycleError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class StudentSoftDeleteResult:
    student: Student
    enrollment_count: int
    clinic_participant_count: int
    user_deactivated: bool


@dataclass(frozen=True)
class StudentRestoreResult:
    student: Student
    restored_ps_number: str | None
    changed_fields: tuple[str, ...]
    user_reactivated: bool
    parent_relinked: bool


def _append_unique(fields: list[str], field: str) -> None:
    if field not in fields:
        fields.append(field)


def _normalize_digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _valid_student_phone(value: Any) -> str | None:
    phone = _normalize_digits(value)
    if len(phone) == 11 and phone.startswith("010"):
        return phone
    return None


def _valid_parent_phone(value: Any) -> str | None:
    phone = _normalize_digits(value)
    if len(phone) >= 11 and phone.startswith("010"):
        return phone[:20]
    return None


def _grade_value(value: Any, school_type: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        grade = int(value)
    except (TypeError, ValueError):
        return None
    return grade if is_valid_grade(school_type or "HIGH", grade) else None


def _deleted_ps_original(ps_number: str | None) -> str | None:
    if not ps_number or not ps_number.startswith("_del_"):
        return None
    parts = ps_number.split("_", 3)
    if len(parts) < 4:
        return None
    return parts[3] or None


def _apply_restore_profile(student: Student, profile_data: dict[str, Any] | None) -> list[str]:
    if not profile_data:
        return []

    changed: list[str] = []

    name = str(profile_data.get("name") or "").strip()
    if name and student.name != name:
        student.name = name
        changed.append("name")

    if "phone" in profile_data or "studentPhone" in profile_data:
        phone = _valid_student_phone(profile_data.get("phone") or profile_data.get("studentPhone"))
        if phone and student.phone != phone:
            student.phone = phone
            changed.append("phone")

    if "parent_phone" in profile_data or "parentPhone" in profile_data:
        parent_phone = _valid_parent_phone(
            profile_data.get("parent_phone") or profile_data.get("parentPhone")
        )
        if parent_phone and student.parent_phone != parent_phone:
            student.parent_phone = parent_phone
            changed.append("parent_phone")

    has_school_data = "school" in profile_data or "school_type" in profile_data
    school_val = str(profile_data.get("school") or "").strip() or None
    if has_school_data:
        st, elementary_school, high_school, middle_school = normalize_school_from_name(
            school_val,
            profile_data.get("school_type"),
        )
        school_updates = {
            "school_type": st,
            "elementary_school": elementary_school,
            "high_school": high_school,
            "middle_school": middle_school,
            "high_school_class": (
                str(profile_data.get("high_school_class") or "").strip() or None
                if st == "HIGH"
                else None
            ),
            "major": (
                str(profile_data.get("major") or "").strip() or None
                if st == "HIGH"
                else None
            ),
        }
        for field, value in school_updates.items():
            if getattr(student, field) != value:
                setattr(student, field, value)
                changed.append(field)

    grade_school_type = student.school_type or "HIGH"
    grade = _grade_value(profile_data.get("grade"), grade_school_type)
    if grade is not None and student.grade != grade:
        student.grade = grade
        changed.append("grade")

    if "memo" in profile_data:
        memo = str(profile_data.get("memo") or "").strip() or None
        if student.memo != memo:
            student.memo = memo
            changed.append("memo")

    if "gender" in profile_data:
        gender = str(profile_data.get("gender") or "").strip().upper()[:1] or None
        gender = gender if gender in ("M", "F") else None
        if student.gender != gender:
            student.gender = gender
            changed.append("gender")

    if "uses_identifier" in profile_data:
        uses_identifier = bool(profile_data.get("uses_identifier"))
        if student.uses_identifier != uses_identifier:
            student.uses_identifier = uses_identifier
            changed.append("uses_identifier")

    return changed


def soft_delete_student(
    student: Student,
    *,
    tenant,
    deleted_at=None,
) -> StudentSoftDeleteResult:
    with transaction.atomic():
        if not tenant or student.tenant_id != tenant.id:
            raise StudentLifecycleError("tenant_mismatch", "학생 테넌트가 일치하지 않습니다.")
        if student.deleted_at:
            raise StudentLifecycleError("already_deleted", "이미 삭제된 학생입니다.")

        deleted_at = deleted_at or timezone.now()
        student.deleted_at = deleted_at
        update_fields = ["deleted_at"]

        if student.ps_number and not student.ps_number.startswith("_del_"):
            student.ps_number = f"_del_{student.id}_{student.ps_number}"
            update_fields.append("ps_number")
        if student.parent_id is not None:
            student.parent_id = None
            update_fields.append("parent")
        student.save(update_fields=update_fields)

        user_deactivated = False
        if student.user:
            student.user.is_active = False
            student.user.token_version = (student.user.token_version or 0) + 1
            user_update = ["is_active", "token_version"]
            if student.user.phone:
                student.user.phone = None
                user_update.append("phone")
            student.user.save(update_fields=user_update)
            TenantMembership.objects.filter(user=student.user, tenant=tenant).update(
                is_active=False
            )
            user_deactivated = True

        from apps.domains.clinic.services.lifecycle import cancel_active_participants_for_student
        from apps.domains.enrollment.services.lifecycle import deactivate_enrollments_for_student

        enrollment_count = deactivate_enrollments_for_student(tenant=tenant, student=student)
        clinic_participant_count = cancel_active_participants_for_student(
            tenant=tenant,
            student=student,
            changed_at=deleted_at,
        )

        return StudentSoftDeleteResult(
            student=student,
            enrollment_count=enrollment_count,
            clinic_participant_count=clinic_participant_count,
            user_deactivated=user_deactivated,
        )


def restore_student(
    student: Student,
    *,
    tenant,
    profile_data: dict[str, Any] | None = None,
) -> StudentRestoreResult:
    with transaction.atomic():
        if not tenant or student.tenant_id != tenant.id:
            raise StudentLifecycleError("tenant_mismatch", "학생 테넌트가 일치하지 않습니다.")
        if not student.deleted_at:
            raise StudentLifecycleError("not_deleted", "삭제된 학생이 아닙니다.")

        changed = _apply_restore_profile(student, profile_data)

        restored_ps_number = _deleted_ps_original(student.ps_number)
        if restored_ps_number:
            if Student.objects.filter(
                tenant=tenant,
                ps_number=restored_ps_number,
                deleted_at__isnull=True,
            ).exclude(pk=student.pk).exists():
                raise StudentLifecycleError(
                    "ps_number_conflict",
                    f"아이디 '{restored_ps_number}'를 이미 사용 중인 활성 학생이 있습니다.",
                )
            student.ps_number = restored_ps_number
            _append_unique(changed, "ps_number")

        student.deleted_at = None
        _append_unique(changed, "deleted_at")
        student.save(update_fields=changed)

        user_reactivated = False
        if student.user:
            user_update = []
            if not student.user.is_active:
                student.user.is_active = True
                user_update.append("is_active")
                user_reactivated = True
            if not student.user.phone and student.phone:
                student.user.phone = student.phone
                user_update.append("phone")
            if user_update:
                student.user.save(update_fields=user_update)
            TenantMembership.ensure_active(tenant=tenant, user=student.user, role="student")

        parent_relinked = False
        if student.parent_phone:
            parent = ensure_parent_for_student(
                tenant=tenant,
                parent_phone=student.parent_phone,
                student_name=student.name,
            )
            if parent and student.parent_id != parent.id:
                student.parent = parent
                student.save(update_fields=["parent"])
                parent_relinked = True

        return StudentRestoreResult(
            student=student,
            restored_ps_number=restored_ps_number,
            changed_fields=tuple(changed),
            user_reactivated=user_reactivated,
            parent_relinked=parent_relinked,
        )
