"""DB read helpers for the clinic-target read model."""

from __future__ import annotations

from typing import Any


def clinic_links_for_admin_targets(*, tenant, include_resolved: bool):
    from apps.domains.progress.models import ClinicLink

    links = (
        ClinicLink.objects.filter(
            is_auto=True,
            tenant=tenant,
        )
        .select_related("session", "session__lecture")
        .order_by("-created_at")
    )
    if not include_resolved:
        links = links.filter(resolved_at__isnull=True)
    return links.filter(enrollment__status="ACTIVE")


def filter_links_by_section(links, *, tenant, section_id: int):
    from django.db import models
    from apps.domains.lectures.models import SectionAssignment

    assigned_enrollment_ids = set(
        SectionAssignment.objects.filter(
            models.Q(class_section_id=section_id) | models.Q(clinic_section_id=section_id),
            tenant=tenant,
        ).values_list("enrollment_id", flat=True)
    )
    return links.filter(enrollment_id__in=assigned_enrollment_ids)


def completed_progress_pairs(*, session_ids: list[int], enrollment_ids: list[int]) -> set[tuple[int, int]]:
    from apps.domains.progress.models import SessionProgress

    return set(
        SessionProgress.objects.filter(
            session_id__in=session_ids,
            enrollment_id__in=enrollment_ids,
            completed=True,
        ).values_list("session_id", "enrollment_id")
    )


def enrollment_map_for_ids(*, tenant, enrollment_ids: list[int]) -> dict[int, Any]:
    from apps.domains.enrollment.models import Enrollment

    return {
        int(enrollment.id): enrollment
        for enrollment in Enrollment.objects.filter(
            id__in=enrollment_ids,
            tenant=tenant,
        ).select_related("student", "lecture")
    }


def student_name_by_enrollment_id(enrollment_id: int) -> str:
    try:
        from apps.domains.enrollments.models import SessionEnrollment  # type: ignore

        session_enrollment = (
            SessionEnrollment.objects.filter(enrollment_id=int(enrollment_id))
            .order_by("-id")
            .first()
        )
        if session_enrollment:
            value = getattr(session_enrollment, "student_name", None)
            if value:
                return str(value)

            student = getattr(session_enrollment, "student", None)
            if student and hasattr(student, "name"):
                return str(getattr(student, "name", "-") or "-")
    except Exception:
        pass

    try:
        from apps.domains.enrollment.models import Enrollment

        enrollment = (
            Enrollment.objects.filter(id=int(enrollment_id))
            .select_related()
            .first()
        )
        if not enrollment:
            return "-"

        student = getattr(enrollment, "student", None)
        if student and hasattr(student, "name"):
            return str(getattr(student, "name", "-") or "-")

        user = getattr(enrollment, "user", None)
        if user:
            name = getattr(user, "name", None) or getattr(user, "username", None)
            return str(name or "-")
    except Exception:
        pass

    return "-"


def regular_homework_for_clinic_target(*, homework_id: int, tenant, session_id: int):
    from apps.domains.homework_results.models import Homework

    return (
        Homework.objects.filter(
            id=int(homework_id),
            tenant=tenant,
            homework_type=Homework.HomeworkType.REGULAR,
            session_id=int(session_id),
        )
        .exclude(meta__removed_from_session_at__isnull=False)
        .first()
    )


def first_homework_score(*, enrollment_id: int, session_id: int, homework_id: int):
    from apps.domains.homework_results.models import HomeworkScore

    return HomeworkScore.objects.filter(
        enrollment_id=int(enrollment_id),
        session_id=int(session_id),
        homework_id=int(homework_id),
        attempt_index=1,
    ).first()


def homework_scores_for_target(*, enrollment_id: int, session_id: int, homework_id: int):
    from apps.domains.homework_results.models import HomeworkScore

    return HomeworkScore.objects.filter(
        enrollment_id=int(enrollment_id),
        session_id=int(session_id),
        homework_id=int(homework_id),
    ).order_by("attempt_index")


def homework_policy_cutline_for_session(*, tenant, session, default: float = 80.0) -> float:
    from apps.domains.homework.models import HomeworkPolicy

    policy = HomeworkPolicy.objects.filter(tenant=tenant, session=session).first()
    if not policy:
        return float(default)
    return float(getattr(policy, "cutline_value", default) or default)


def regular_exam_for_source(*, exam_id: int, tenant, session_id: int):
    from apps.domains.exams.models import Exam

    return Exam.objects.filter(
        id=int(exam_id),
        tenant=tenant,
        exam_type=Exam.ExamType.REGULAR,
        is_active=True,
        sessions__id=int(session_id),
    ).first()


def recent_sessions_for_tenant(*, tenant, cutoff):
    from apps.domains.lectures.models import Session

    return (
        Session.objects.filter(lecture__tenant=tenant, date__gte=cutoff)
        .select_related("lecture")
    )


def active_enrollment_ids_by_lecture(*, tenant, lecture_ids: list[int]) -> dict[int, set[int]]:
    from apps.domains.enrollment.models import Enrollment

    enrollments_by_lecture: dict[int, set[int]] = {}
    for row in Enrollment.objects.filter(
        lecture_id__in=lecture_ids,
        tenant=tenant,
        status="ACTIVE",
    ).values("id", "lecture_id"):
        enrollments_by_lecture.setdefault(row["lecture_id"], set()).add(row["id"])
    return enrollments_by_lecture


def existing_clinic_link_pairs_for_sessions(session_ids) -> set[tuple[int, int]]:
    from apps.domains.progress.models import ClinicLink

    return set(
        ClinicLink.objects.filter(
            session_id__in=session_ids,
        ).values_list("session_id", "enrollment_id")
    )
