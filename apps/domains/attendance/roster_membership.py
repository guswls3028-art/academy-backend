"""Transactional session roster changes shared by the API and the Excel worker."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from apps.domains.attendance.models import Attendance
from apps.support.attendance.roster_dependencies import auto_assign_roster_fees


class RosterMembershipError(ValueError):
    """The requested roster change conflicts with the current student or enrollment."""


@dataclass(frozen=True)
class SessionRosterMembership:
    session_enrollment: Any
    attendance: Attendance


@transaction.atomic
def write_session_roster_membership(
    *, tenant, session, enrollment, allow_session_reregistration: bool = False,
) -> SessionRosterMembership:
    """
    Ensure one enrollment is active on one session roster and has attendance.

    This is the shared unit used by both attendance roster creation and the
    session-enrollment bulk endpoint, so fee reactivation and attendance
    idempotency cannot drift between the two public APIs. Only explicit roster
    registration may restore a retained SECESSION attendance row.
    """
    if tenant is None:
        raise RosterMembershipError("tenant가 필요합니다.")

    if getattr(session.lecture, "tenant_id", None) != tenant.id:
        raise RosterMembershipError("다른 학원의 세션입니다.")

    locked_student_ids = enroll_repo.lock_active_student_ids_for_tenant(
        [enrollment.student_id],
        tenant,
    )
    if locked_student_ids != (enrollment.student_id,):
        raise RosterMembershipError("삭제된 학생은 출결 명단에 추가할 수 없습니다.")

    current_enrollment = enroll_repo.lock_enrollment_by_id_with_lecture(
        enrollment.id,
        tenant,
    )
    if current_enrollment is None:
        raise RosterMembershipError("수강 등록을 찾을 수 없습니다.")
    if current_enrollment.student_id != enrollment.student_id:
        raise RosterMembershipError("수강 등록의 학생 정보가 변경되었습니다.")
    if current_enrollment.lecture_id != session.lecture_id:
        raise RosterMembershipError("다른 강의 수강자는 이 세션에 추가할 수 없습니다.")

    enrollment = current_enrollment
    if enrollment.status != "ACTIVE":
        raise RosterMembershipError(
            "비활성 또는 대기 중인 수강 등록은 출결 명단에서 자동으로 "
            "재활성화할 수 없습니다. 먼저 수강 등록 화면에서 명시적으로 "
            "재등록해 주세요."
        )

    attendance, _ = enroll_repo.attendance_get_or_create_tenant(
        tenant=tenant,
        enrollment=enrollment,
        session=session,
        defaults={"status": "UNSET"},
        for_update=True,
    )
    if attendance.status == "SECESSION" and not allow_session_reregistration:
        raise RosterMembershipError("퇴원한 차시는 수강생 등록 화면에서 명시적으로 재등록해 주세요.")

    auto_assign_roster_fees(
        tenant=tenant,
        student=enrollment.student,
        lecture=session.lecture,
        enrollment=enrollment,
    )

    session_enrollment, _ = enroll_repo.session_enrollment_get_or_create_tenant(
        tenant=tenant,
        session=session,
        enrollment=enrollment,
    )
    if attendance.status == "SECESSION":
        attendance.status = "UNSET"
        attendance.save(update_fields=["status"])
    return SessionRosterMembership(
        session_enrollment=session_enrollment,
        attendance=attendance,
    )
