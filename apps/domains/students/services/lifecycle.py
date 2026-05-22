from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.core.models import TenantMembership
from apps.domains.students.models import Student


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
