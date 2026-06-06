from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from ..models import Student, StudentRegistrationRequest
from .creation import create_student_account
from .identity import StudentIdentityError, derive_student_omr_code, resolve_student_login_id


class RegistrationApprovalError(ValueError):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class RegistrationApprovalNotice:
    student_name: str
    student_phone: str
    student_id: str
    student_password: str
    parent_phone: str
    parent_password: str


@dataclass(frozen=True)
class RegistrationApprovalResult:
    registration: StudentRegistrationRequest
    student: Student
    notice: RegistrationApprovalNotice


def _resolve_login_id(tenant, reg: StudentRegistrationRequest) -> str:
    try:
        return resolve_student_login_id(
            tenant=tenant,
            requested_id=reg.username,
            phone=reg.phone,
            requested_conflict="fallback",
        )
    except StudentIdentityError as exc:
        raise RegistrationApprovalError(str(exc.detail), status_code=400) from exc


def approve_registration_request(
    *,
    tenant,
    registration_id: int,
) -> RegistrationApprovalResult:
    """
    Approve one student registration request.

    Owns only the durable state transition and account creation graph. HTTP response
    shape and message delivery remain caller concerns.
    """
    with transaction.atomic():
        # Lock only the registration row. Joining nullable student here breaks
        # PostgreSQL FOR UPDATE because it becomes a nullable outer join.
        reg = (
            StudentRegistrationRequest.objects.select_for_update()
            .get(pk=registration_id, tenant=tenant)
        )
        if reg.status != StudentRegistrationRequest.PENDING:
            raise RegistrationApprovalError("이미 처리된 신청입니다.", status_code=400)

        ps_number = _resolve_login_id(tenant, reg)
        parent_phone = reg.parent_phone or ""
        student_phone = reg.phone or None
        result = create_student_account(
            tenant=tenant,
            password_hash=reg.initial_password,
            student_data={
                "name": reg.name,
                "parent_phone": parent_phone,
                "phone": student_phone,
                "ps_number": ps_number,
                "omr_code": derive_student_omr_code(
                    phone=student_phone,
                    parent_phone=parent_phone,
                ),
                "uses_identifier": not (student_phone and student_phone.strip()),
                "school_type": reg.school_type,
                "elementary_school": reg.elementary_school or None,
                "high_school": reg.high_school or None,
                "middle_school": reg.middle_school or None,
                "high_school_class": reg.high_school_class or None,
                "major": reg.major or None,
                "grade": reg.grade,
                "gender": reg.gender or None,
                "memo": reg.memo or None,
                "address": reg.address or None,
                "origin_middle_school": reg.origin_middle_school or None,
            },
        )

        reg.status = StudentRegistrationRequest.APPROVED
        reg.student = result.student
        reg.initial_password_plain = ""
        reg.save(update_fields=["status", "student", "initial_password_plain", "updated_at"])

    notice = RegistrationApprovalNotice(
        student_name=reg.name,
        student_phone=student_phone or "",
        student_id=ps_number,
        student_password="가입 신청 시 입력한 비밀번호",
        parent_phone=parent_phone,
        parent_password=result.parent_password_for_notice or "변경되지 않음",
    )
    return RegistrationApprovalResult(
        registration=reg,
        student=result.student,
        notice=notice,
    )
