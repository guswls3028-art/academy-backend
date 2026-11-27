"""Canonical attendance boundary for exam and homework learning todos.

An actual ``Attendance.status == ABSENT`` means the learner has neither an
onsite nor video/material obligation for that session. ``ONLINE`` is the
video-requested learning state and remains actionable for both assessments.
A retained roster with SECESSION has no new learning obligations after a
different session is restored. Missing attendance still follows the active roster.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.db.models import F


INELIGIBLE_LEARNING_TODO_STATUSES = frozenset({"ABSENT", "SECESSION"})


def attendance_status_is_learning_todo_eligible(status: str | None) -> bool:
    return str(status or "").upper() not in INELIGIBLE_LEARNING_TODO_STATUSES


def attendance_status_map(
    *,
    tenant: Any,
    session: Any,
    enrollment_ids: Iterable[int],
) -> dict[int, str]:
    from apps.domains.attendance.models import Attendance

    normalized_ids = {int(enrollment_id) for enrollment_id in enrollment_ids}
    if not normalized_ids:
        return {}
    return {
        int(enrollment_id): str(status)
        for enrollment_id, status in Attendance.objects.filter(
            tenant=tenant,
            session=session,
            enrollment_id__in=normalized_ids,
            enrollment__tenant=tenant,
            enrollment__lecture=session.lecture,
        ).values_list("enrollment_id", "status")
    }


def eligible_learning_todo_enrollment_ids(
    *,
    tenant: Any,
    session: Any,
) -> set[int]:
    """Use the same eligible roster as the assignment picker in one query."""
    return set(
        eligible_session_enrollments(tenant=tenant, session_id=session.pk)
        .order_by().values_list("enrollment_id", flat=True)
    )


def eligible_session_enrollments(
    *, tenant: Any, session_id: int, excluded_statuses: Iterable[str] = INELIGIBLE_LEARNING_TODO_STATUSES,
):
    """Return active session rows with the caller's attendance exclusions."""
    from apps.domains.attendance.models import Attendance
    from apps.domains.enrollment.models import SessionEnrollment
    excluded_ids = Attendance.objects.filter(
        tenant=tenant,
        session_id=session_id,
        status__in=excluded_statuses,
    ).values_list("enrollment_id", flat=True)
    return (
        SessionEnrollment.objects.filter(
            tenant=tenant,
            session_id=session_id,
            session__lecture__tenant=tenant,
            enrollment__tenant=tenant,
            enrollment__lecture_id=F("session__lecture_id"),
            enrollment__status="ACTIVE",
            enrollment__student__deleted_at__isnull=True,
        )
        .exclude(enrollment_id__in=excluded_ids)
        .select_related("enrollment", "enrollment__student", "enrollment__lecture")
        .order_by("enrollment_id")
    )


def learning_todo_eligible_pairs(
    *,
    tenant: Any,
    enrollment_session_pairs: Iterable[tuple[int, int]],
) -> set[tuple[int, int]]:
    """Batch-filter exact (enrollment, session) pairs for read projections."""
    from apps.domains.enrollment.models import Enrollment

    pairs = {
        (int(enrollment_id), int(session_id))
        for enrollment_id, session_id in enrollment_session_pairs
    }
    if not pairs:
        return set()
    enrollment_ids = {pair[0] for pair in pairs}
    session_ids = {pair[1] for pair in pairs}
    valid_pairs = {
        (int(enrollment_id), int(session_id))
        for enrollment_id, session_id in Enrollment.objects.filter(
            id__in=enrollment_ids,
            tenant=tenant,
            status="ACTIVE",
            student__deleted_at__isnull=True,
            lecture__tenant=tenant,
            lecture__sessions__id__in=session_ids,
        ).values_list("id", "lecture__sessions__id")
        if (int(enrollment_id), int(session_id)) in pairs
    }
    if not valid_pairs:
        return set()
    return valid_pairs - ineligible_learning_todo_pairs(
        tenant=tenant,
        enrollment_session_pairs=valid_pairs,
    )


def ineligible_learning_todo_pairs(
    *,
    tenant: Any,
    enrollment_session_pairs: Iterable[tuple[int, int]],
) -> set[tuple[int, int]]:
    """Find absent or withdrawn pairs within the requested enrollment and session bounds."""
    from apps.domains.attendance.models import Attendance

    pairs = {
        (int(enrollment_id), int(session_id))
        for enrollment_id, session_id in enrollment_session_pairs
    }
    if not pairs:
        return set()
    return {
        (int(enrollment_id), int(session_id))
        for enrollment_id, session_id in Attendance.objects.filter(
            tenant=tenant,
            enrollment_id__in={pair[0] for pair in pairs},
            session_id__in={pair[1] for pair in pairs},
            status__in=INELIGIBLE_LEARNING_TODO_STATUSES,
            enrollment__tenant=tenant,
            session__lecture__tenant=tenant,
            enrollment__lecture_id=F("session__lecture_id"),
        ).values_list("enrollment_id", "session_id")
        if (int(enrollment_id), int(session_id)) in pairs
    }


def enrollment_session_is_learning_todo_eligible(
    *,
    tenant_id: int,
    enrollment_id: int,
    session_id: int,
) -> bool:
    from apps.domains.attendance.models import Attendance
    from apps.domains.enrollment.models import Enrollment

    in_session_scope = Enrollment.objects.filter(
        id=int(enrollment_id),
        tenant_id=int(tenant_id),
        status="ACTIVE",
        student__deleted_at__isnull=True,
        lecture__tenant_id=int(tenant_id),
        lecture__sessions__id=int(session_id),
    ).exists()
    if not in_session_scope:
        return False
    status = Attendance.objects.filter(
        tenant_id=int(tenant_id),
        session_id=int(session_id),
        enrollment_id=int(enrollment_id),
    ).values_list("status", flat=True).first()
    return attendance_status_is_learning_todo_eligible(status)


def exam_is_learning_todo_eligible(
    *,
    tenant_id: int,
    enrollment_id: int,
    exam_id: int,
) -> bool:
    """A shared exam remains actionable when any linked session is eligible."""
    from apps.domains.attendance.models import Attendance
    from apps.domains.enrollment.models import Enrollment, SessionEnrollment
    from apps.domains.exams.models import ExamEnrollment
    from apps.domains.lectures.models import Session

    enrollment = Enrollment.objects.filter(
        id=int(enrollment_id),
        tenant_id=int(tenant_id),
        status="ACTIVE",
        student__deleted_at__isnull=True,
    ).only("id", "lecture_id").first()
    if enrollment is None:
        return False

    session_ids = {
        int(session_id)
        for session_id in SessionEnrollment.objects.filter(
            tenant_id=int(tenant_id),
            enrollment_id=int(enrollment_id),
            enrollment__tenant_id=int(tenant_id),
            enrollment__status="ACTIVE",
            enrollment__student__deleted_at__isnull=True,
            enrollment__lecture_id=F("session__lecture_id"),
            session__lecture__tenant_id=int(tenant_id),
            session__exams__id=int(exam_id),
        ).values_list("session_id", flat=True)
    }
    session_ids.update(
        int(session_id)
        for session_id in Attendance.objects.filter(
            tenant_id=int(tenant_id),
            enrollment_id=int(enrollment_id),
            enrollment__tenant_id=int(tenant_id),
            enrollment__status="ACTIVE",
            enrollment__student__deleted_at__isnull=True,
            enrollment__lecture_id=F("session__lecture_id"),
            session__lecture__tenant_id=int(tenant_id),
            session__exams__id=int(exam_id),
        ).values_list("session_id", flat=True)
    )
    if not session_ids and ExamEnrollment.objects.filter(
        exam_id=int(exam_id),
        enrollment_id=int(enrollment_id),
    ).exists():
        session_ids = {
            int(session_id)
            for session_id in Session.objects.filter(
                exams__id=int(exam_id),
                lecture_id=int(enrollment.lecture_id),
                lecture__tenant_id=int(tenant_id),
            ).values_list("id", flat=True)
        }
        if not session_ids:
            return True

    if not session_ids:
        return False
    absent_session_ids = set(
        Attendance.objects.filter(
            tenant_id=int(tenant_id),
            enrollment_id=int(enrollment_id),
            session_id__in=session_ids,
            status__in=INELIGIBLE_LEARNING_TODO_STATUSES,
        ).values_list("session_id", flat=True)
    )
    return bool(session_ids - absent_session_ids)


def reconcile_learning_todo_targets(attendance: Any) -> None:
    """Materialize newly eligible targets while preserving historical data.

    The caller owns the attendance-row transaction/lock. ABSENT never deletes
    assignments, submissions, results, manual grades, or ClinicLink history;
    read projections use this module to suppress only the current todo.
    """
    if (
        str(attendance.status).upper() in {"INACTIVE", "SECESSION"}
        or not attendance_status_is_learning_todo_eligible(attendance.status)
    ):
        return

    from apps.domains.exams.models import Exam, ExamEnrollment
    from apps.domains.homework.models import HomeworkAssignment
    from apps.domains.homework_results.models import Homework
    from apps.domains.progress.services.clinic_trigger_service import (
        ClinicTriggerService,
    )
    from apps.domains.progress.services.session_calculator import (
        SessionProgressCalculator,
    )
    from apps.domains.progress.models import SessionProgress

    enrollment = attendance.enrollment
    session = attendance.session
    tenant = attendance.tenant
    if (
        int(enrollment.tenant_id) != int(tenant.id)
        or int(enrollment.lecture_id) != int(session.lecture_id)
        or enrollment.status != "ACTIVE"
    ):
        return

    exam_ids = list(
        Exam.objects.filter(
            tenant=tenant,
            sessions=session,
            exam_type=Exam.ExamType.REGULAR,
            is_active=True,
        ).values_list("id", flat=True)
    )
    ExamEnrollment.objects.bulk_create(
        [
            ExamEnrollment(exam_id=int(exam_id), enrollment=enrollment)
            for exam_id in exam_ids
        ],
        ignore_conflicts=True,
    )

    homeworks = Homework.objects.filter(
        tenant=tenant,
        session=session,
        homework_type=Homework.HomeworkType.REGULAR,
    ).exclude(meta__removed_from_session_at__isnull=False)
    HomeworkAssignment.objects.bulk_create(
        [
            HomeworkAssignment(
                tenant=tenant,
                homework_id=int(homework_id),
                session=session,
                enrollment=enrollment,
            )
            for homework_id in homeworks.values_list("id", flat=True)
        ],
        ignore_conflicts=True,
    )

    existing_progress = SessionProgress.objects.filter(
        enrollment=enrollment,
        session=session,
    ).first()
    progress = SessionProgressCalculator.calculate(
        enrollment_id=int(enrollment.id),
        session=session,
        attendance_type=(
            SessionProgress.AttendanceType.ONLINE
            if str(attendance.status).upper() == "ONLINE"
            else SessionProgress.AttendanceType.OFFLINE
        ),
        video_progress_rate=(
            int(existing_progress.video_progress_rate or 0)
            if existing_progress is not None
            else 0
        ),
        homework_submitted=(
            bool(existing_progress.homework_submitted)
            if existing_progress is not None
            else False
        ),
    )
    ClinicTriggerService.auto_create_if_failed(progress)
