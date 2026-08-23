from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q

from apps.core.models.user import user_internal_username
from apps.support.students.lifecycle_dependencies import parent_account_by_phone_for_registration

from ..models import Student, StudentRegistrationRequest
from .creation import create_student_account
from .identity import (
    StudentIdentityError,
    derive_student_omr_code,
    phone_digits,
    resolve_student_login_id,
)
from .registration_policy import is_student_self_registration_enabled


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
            requested_conflict="error",
        )
    except StudentIdentityError as exc:
        raise RegistrationApprovalError(str(exc.detail), status_code=400) from exc


def _registration_identity_query(tenant, reg: StudentRegistrationRequest) -> Q:
    query = Q(
        name=str(reg.name or "").strip(),
        parent_phone=phone_digits(reg.parent_phone),
    )
    student_phone = phone_digits(reg.phone)
    if student_phone:
        query |= Q(phone=student_phone) | Q(user__phone=student_phone)
    requested_id = str(reg.username or "").strip()
    if requested_id:
        query |= Q(ps_number=requested_id) | Q(
            user__username=user_internal_username(tenant, requested_id)
        )
    return query


def _validate_existing_student_graph(*, tenant, reg, student, locked_user) -> None:
    if student.tenant_id != tenant.id or locked_user.tenant_id != tenant.id:
        raise RegistrationApprovalError(
            "기존 학생 계정의 테넌트 연결이 일치하지 않습니다.",
            status_code=409,
        )
    if student.user_id != locked_user.id:
        raise RegistrationApprovalError(
            "기존 학생 계정 연결이 승인 중 변경되었습니다.",
            status_code=409,
        )
    if student.deleted_at is not None:
        raise RegistrationApprovalError(
            "같은 식별값의 삭제 학생이 있습니다. 기존 학생을 확인해 주세요.",
            status_code=409,
        )
    if str(student.name or "").strip() != str(reg.name or "").strip():
        raise RegistrationApprovalError(
            "가입 신청 정보와 기존 학생 이름이 일치하지 않습니다.",
            status_code=409,
        )

    registration_parent_phone = phone_digits(reg.parent_phone)
    if phone_digits(student.parent_phone) != registration_parent_phone:
        raise RegistrationApprovalError(
            "가입 신청 정보와 기존 학생의 학부모 연락처가 일치하지 않습니다.",
            status_code=409,
        )

    parent = student.parent
    if parent is None:
        raise RegistrationApprovalError(
            "기존 학생의 학부모 계정 연결을 먼저 확인해 주세요.",
            status_code=409,
        )
    if parent.tenant_id != tenant.id or phone_digits(parent.phone) != registration_parent_phone:
        raise RegistrationApprovalError(
            "기존 학부모 계정 연결이 가입 신청 정보와 일치하지 않습니다.",
            status_code=409,
        )
    if parent.user_id:
        if parent.user.tenant_id != tenant.id or phone_digits(parent.user.phone) != registration_parent_phone:
            raise RegistrationApprovalError(
                "기존 학부모 로그인 계정의 테넌트 또는 연락처가 일치하지 않습니다.",
                status_code=409,
            )

    stored_student_phone = phone_digits(student.phone)
    stored_user_phone = phone_digits(locked_user.phone)
    if stored_student_phone != stored_user_phone:
        raise RegistrationApprovalError(
            "기존 학생과 로그인 계정의 연락처 연결이 일치하지 않습니다.",
            status_code=409,
        )
    registration_student_phone = phone_digits(reg.phone)
    existing_identity_phones = {
        value
        for value in (stored_student_phone, registration_parent_phone)
        if value
    }
    if registration_student_phone and registration_student_phone not in existing_identity_phones:
        raise RegistrationApprovalError(
            "가입 신청 정보와 기존 학생·학부모 연락처가 일치하지 않습니다.",
            status_code=409,
        )


def _resolve_existing_student(*, tenant, reg: StudentRegistrationRequest) -> Student | None:
    identity_query = _registration_identity_query(tenant, reg)
    candidates = list(
        Student.objects.filter(tenant=tenant)
        .filter(identity_query)
        .values("id", "user_id", "deleted_at")
        .order_by("id")[:3]
    )
    deleted = [candidate for candidate in candidates if candidate["deleted_at"] is not None]
    if deleted:
        raise RegistrationApprovalError(
            "같은 식별값의 삭제 학생이 있습니다. 새 계정을 만들지 말고 기존 학생을 확인해 주세요.",
            status_code=409,
        )
    if len(candidates) > 1:
        raise RegistrationApprovalError(
            "같은 가입 식별값을 가진 활성 학생이 여러 명입니다. 학생 정보를 먼저 정리해 주세요.",
            status_code=409,
        )
    if not candidates:
        return None

    candidate = candidates[0]
    User = get_user_model()
    # #277 identity lifecycle contract: lock persisted User before Student.
    locked_user = User.objects.select_for_update().get(pk=candidate["user_id"])
    # Keep nullable Parent/User joins out of the locking query; PostgreSQL
    # rejects FOR UPDATE on the nullable side of an outer join.
    student = Student.objects.select_for_update().get(pk=candidate["id"])
    if not Student.objects.filter(pk=student.pk, tenant=tenant).filter(identity_query).exists():
        raise RegistrationApprovalError(
            "기존 학생 식별정보가 승인 중 변경되었습니다. 다시 확인해 주세요.",
            status_code=409,
        )
    _validate_existing_student_graph(
        tenant=tenant,
        reg=reg,
        student=student,
        locked_user=locked_user,
    )
    if StudentRegistrationRequest.objects.filter(student=student).exclude(pk=reg.pk).exists():
        raise RegistrationApprovalError(
            "기존 학생이 이미 다른 가입 신청과 연결되어 있습니다.",
            status_code=409,
        )
    return student


def _validate_unlinked_account_graph(*, tenant, reg: StudentRegistrationRequest) -> None:
    registration_parent_phone = phone_digits(reg.parent_phone)
    parent = parent_account_by_phone_for_registration(
        tenant_id=tenant.id,
        phone=registration_parent_phone,
    )
    if parent and parent.user_id and parent.user.tenant_id != tenant.id:
        raise RegistrationApprovalError(
            "기존 학부모 계정의 테넌트 연결이 일치하지 않습니다.",
            status_code=409,
        )

    student_phone = phone_digits(reg.phone)
    if not student_phone:
        return
    users = list(
        get_user_model().objects.filter(tenant=tenant, phone=student_phone)
        .select_related("student_profile", "parent_profile")
        .order_by("id")[:3]
    )
    for user in users:
        parent_profile = getattr(user, "parent_profile", None)
        if (
            parent_profile is not None
            and parent_profile.tenant_id == tenant.id
            and phone_digits(parent_profile.phone) == registration_parent_phone
        ):
            continue
        raise RegistrationApprovalError(
            "같은 학생 연락처를 사용하는 기존 계정이 있습니다. 계정 연결을 먼저 확인해 주세요.",
            status_code=409,
        )


def _approve_with_existing_student(
    *,
    reg: StudentRegistrationRequest,
    student: Student,
) -> RegistrationApprovalResult:
    reg.status = StudentRegistrationRequest.APPROVED
    reg.student = student
    reg.initial_password_plain = ""
    reg.save(update_fields=["status", "student", "initial_password_plain", "updated_at"])
    notice = RegistrationApprovalNotice(
        student_name=student.name,
        student_phone=student.phone or "",
        student_id=student.ps_number,
        student_password="변경되지 않음",
        parent_phone=student.parent_phone,
        parent_password="변경되지 않음",
    )
    return RegistrationApprovalResult(
        registration=reg,
        student=student,
        notice=notice,
    )


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
        if not is_student_self_registration_enabled(tenant):
            raise RegistrationApprovalError(
                "이 학원은 운영정책상 학생 회원가입을 사용하지 않습니다.",
                status_code=403,
            )
        # Lock only the registration row. Joining nullable student here breaks
        # PostgreSQL FOR UPDATE because it becomes a nullable outer join.
        reg = (
            StudentRegistrationRequest.objects.select_for_update()
            .get(pk=registration_id, tenant=tenant)
        )
        if reg.status != StudentRegistrationRequest.PENDING:
            raise RegistrationApprovalError("이미 처리된 신청입니다.", status_code=400)

        existing_student = _resolve_existing_student(tenant=tenant, reg=reg)
        if existing_student is not None:
            return _approve_with_existing_student(reg=reg, student=existing_student)

        _validate_unlinked_account_graph(tenant=tenant, reg=reg)

        ps_number = _resolve_login_id(tenant, reg)
        parent_phone = reg.parent_phone or ""
        student_phone = reg.phone or None
        result = create_student_account(
            tenant=tenant,
            password_hash=reg.initial_password,
            account_notice_student_password="가입 신청 시 입력한 비밀번호",
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

    created_student = result.student
    notice = RegistrationApprovalNotice(
        student_name=reg.name,
        student_phone=created_student.phone or "",
        student_id=created_student.ps_number,
        student_password="가입 신청 시 입력한 비밀번호",
        parent_phone=parent_phone,
        parent_password=result.parent_password_for_notice or "변경되지 않음",
    )
    return RegistrationApprovalResult(
        registration=reg,
        student=result.student,
        notice=notice,
    )
