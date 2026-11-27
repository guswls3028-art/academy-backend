"""Canonical attendance roster write entrypoints."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from apps.domains.attendance.models import Attendance
from apps.domains.attendance.roster_membership import (
    RosterMembershipError,
    SessionRosterMembership,
    write_session_roster_membership,
)
from apps.support.attendance.roster_dependencies import require_attendance_tenant


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


def ensure_session_roster_membership(
    *, tenant, session, enrollment, allow_session_reregistration: bool = False,
) -> SessionRosterMembership:
    try:
        return write_session_roster_membership(
            tenant=tenant, session=session, enrollment=enrollment,
            allow_session_reregistration=allow_session_reregistration,
        )
    except RosterMembershipError as exc:
        raise ValidationError({"detail": str(exc)}) from exc


@transaction.atomic
def create_attendance_roster(*, tenant, session_id, student_ids) -> list[Attendance]:
    tenant = require_attendance_tenant(tenant)
    normalized_session_id = _normalize_session_id(session_id)
    normalized_student_ids = _normalize_student_ids(student_ids)

    session = enroll_repo.get_session_by_id_with_lecture(normalized_session_id)
    if not session or session.lecture.tenant_id != tenant.id:
        raise NotFound("세션을 찾을 수 없습니다.")

    locked_student_ids = set(
        enroll_repo.lock_active_student_ids_for_tenant(
            normalized_student_ids,
            tenant,
        )
    )
    invalid_student_ids = [
        sid for sid in normalized_student_ids if sid not in locked_student_ids
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
            allow_session_reregistration=True,
        )
        attendances.append(membership.attendance)

    return attendances
