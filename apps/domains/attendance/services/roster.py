"""Canonical attendance roster write entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.selectors import require_tenant
from apps.domains.fees.services import auto_assign_fees_on_enrollment
from apps.domains.students.selectors import students_for_tenant


@dataclass(frozen=True)
class SessionRosterMembership:
    session_enrollment: Any
    attendance: Attendance


def _normalize_student_ids(value) -> list[int]:
    if not isinstance(value, list):
        raise ValidationError({"detail": "session, students(list)는 필수입니다"})
    try:
        return [int(sid) for sid in value]
    except (TypeError, ValueError) as exc:
        raise ValidationError({"detail": "학생 ID 값이 잘못되었습니다."}) from exc


def _normalize_session_id(value) -> int:
    if not value:
        raise ValidationError({"detail": "session, students(list)는 필수입니다"})
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"detail": "session 값이 잘못되었습니다."}) from exc


def ensure_session_roster_membership(*, tenant, session, enrollment) -> SessionRosterMembership:
    """
    Ensure one enrollment is active on one session roster and has attendance.

    This is the shared unit used by both attendance roster creation and the
    session-enrollment bulk endpoint, so fee reactivation and attendance
    idempotency cannot drift between the two public APIs.
    """
    tenant = require_tenant(tenant)

    if getattr(session.lecture, "tenant_id", None) != tenant.id:
        raise ValidationError({"detail": "다른 학원의 세션입니다."})
    if enrollment.tenant_id != tenant.id:
        raise ValidationError({"detail": "수강 등록을 찾을 수 없습니다."})
    if enrollment.lecture_id != session.lecture_id:
        raise ValidationError({"detail": "다른 강의 수강자는 이 세션에 추가할 수 없습니다."})

    if enrollment.status != "ACTIVE":
        enrollment.status = "ACTIVE"
        enrollment.save(update_fields=["status"])

    auto_assign_fees_on_enrollment(
        tenant,
        enrollment.student,
        session.lecture,
        enrollment,
    )

    session_enrollment, _ = enroll_repo.session_enrollment_get_or_create_tenant(
        tenant=tenant,
        session=session,
        enrollment=enrollment,
    )
    attendance, _ = enroll_repo.attendance_get_or_create_tenant(
        tenant=tenant,
        enrollment=enrollment,
        session=session,
        defaults={"status": "PRESENT"},
    )
    return SessionRosterMembership(
        session_enrollment=session_enrollment,
        attendance=attendance,
    )


@transaction.atomic
def create_attendance_roster(*, tenant, session_id, student_ids) -> list[Attendance]:
    tenant = require_tenant(tenant)
    normalized_session_id = _normalize_session_id(session_id)
    normalized_student_ids = _normalize_student_ids(student_ids)

    session = enroll_repo.get_session_by_id_with_lecture(normalized_session_id)
    if not session or session.lecture.tenant_id != tenant.id:
        raise NotFound("세션을 찾을 수 없습니다.")

    valid_student_ids = set(
        students_for_tenant(tenant, deleted="active")
        .filter(id__in=normalized_student_ids)
        .values_list("id", flat=True)
    )
    invalid_student_ids = [
        sid for sid in normalized_student_ids if sid not in valid_student_ids
    ]
    if invalid_student_ids:
        raise ValidationError(
            {"detail": f"이 학원에 속하지 않는 학생 ID: {invalid_student_ids}"}
        )

    attendances: list[Attendance] = []
    for student_id in normalized_student_ids:
        enrollment, _ = enroll_repo.enrollment_get_or_create(
            tenant=tenant,
            lecture=session.lecture,
            student_id=student_id,
            defaults={"status": "ACTIVE"},
        )
        membership = ensure_session_roster_membership(
            tenant=tenant,
            session=session,
            enrollment=enrollment,
        )
        attendances.append(membership.attendance)

    return attendances
