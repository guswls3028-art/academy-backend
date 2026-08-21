"""DB read helpers for the clinic-target read model."""

from __future__ import annotations

from typing import Any


def explicit_not_submitted_exam_results(*, tenant):
    """Latest exam result per enrollment, only when it is explicitly absent."""
    from apps.domains.results.models import Result
    from django.db.models import F, OuterRef, Subquery

    latest_result_id = (
        Result.objects.filter(
            target_type="exam",
            target_id=OuterRef("target_id"),
            enrollment_id=OuterRef("enrollment_id"),
        )
        .order_by("-id")
        .values("id")[:1]
    )
    return Result.objects.filter(
        id=Subquery(latest_result_id),
        target_type="exam",
        target_id=F("attempt__exam_id"),
        enrollment__tenant=tenant,
        enrollment__student__tenant=tenant,
        enrollment__lecture__tenant=tenant,
        enrollment__status="ACTIVE",
        attempt__meta__status="NOT_SUBMITTED",
        attempt__exam__tenant=tenant,
        attempt__exam__exam_type="regular",
        attempt__exam__is_active=True,
    )


def explicit_not_submitted_exam_targets(*, tenant, section_id: int | None = None):
    """Return exact roster/session rows for exams explicitly marked NOT_SUBMITTED.

    A missing score alone is intentionally insufficient: this projection only exposes
    a staff-authored absence marker and never infers absence from an empty result.
    Existing source-specific ClinicLink history suppresses the projection, including
    an already waived case.
    """
    from apps.domains.enrollment.models import SessionEnrollment
    from apps.domains.lectures.models import Session
    from apps.domains.progress.models import ClinicLink
    from django.db.models import Q

    results = list(
        explicit_not_submitted_exam_results(tenant=tenant).select_related(
            "attempt__exam",
            "enrollment__student",
            "enrollment__lecture",
        )
    )
    if not results:
        return []

    enrollment_ids = {int(row.enrollment_id) for row in results if row.enrollment_id}
    if section_id:
        from apps.domains.lectures.models import SectionAssignment

        allowed_ids = set(
            SectionAssignment.objects.filter(
                tenant=tenant,
            ).filter(
                Q(class_section_id=int(section_id))
                | Q(clinic_section_id=int(section_id))
            ).values_list("enrollment_id", flat=True)
        )
        enrollment_ids &= allowed_ids
        results = [
            row for row in results if int(row.enrollment_id or 0) in enrollment_ids
        ]
        if not results:
            return []

    exam_ids = {int(row.target_id) for row in results}
    roster_links = list(
        SessionEnrollment.objects.filter(
            tenant=tenant,
            enrollment_id__in=enrollment_ids,
            session__lecture__tenant=tenant,
            session__exams__id__in=exam_ids,
        ).values_list("enrollment_id", "session_id", "session__exams__id").distinct()
    )
    session_ids = {int(session_id) for _, session_id, _ in roster_links}
    sessions = {
        int(session.id): session
        for session in Session.objects.filter(
            id__in=session_ids,
            lecture__tenant=tenant,
        ).select_related("lecture")
    }
    existing = set(
        ClinicLink.objects.filter(
            tenant=tenant,
            enrollment_id__in=enrollment_ids,
            session_id__in=session_ids,
            source_type="exam",
            source_id__in=exam_ids,
        ).values_list("enrollment_id", "session_id", "source_id")
    )

    result_by_key = {
        (int(row.enrollment_id), int(row.target_id)): row for row in results
    }
    targets = []
    for enrollment_id, session_id, exam_id in roster_links:
        key = (int(enrollment_id), int(exam_id))
        result = result_by_key.get(key)
        session = sessions.get(int(session_id))
        if not result or not session:
            continue
        if (int(enrollment_id), int(session_id), int(exam_id)) in existing:
            continue
        targets.append((result, session))
    return targets


def clinic_links_for_admin_targets(*, tenant, include_resolved: bool):
    from apps.domains.progress.models import ClinicLink

    links = (
        ClinicLink.objects.filter(
            is_auto=True,
            tenant=tenant,
            enrollment__tenant=tenant,
            enrollment__student__tenant=tenant,
            enrollment__lecture__tenant=tenant,
            session__lecture__tenant=tenant,
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


def homework_cutline_settings_for_target(*, session, homework=None):
    """Read the exact per-homework-or-session-fallback grading contract."""
    from apps.domains.homework.utils.homework_policy import (
        resolve_homework_cutline_settings,
    )

    return resolve_homework_cutline_settings(
        session=session,
        homework=homework,
        create_policy=False,
    )


def regular_exam_for_source(*, exam_id: int, tenant, session_id: int):
    from apps.domains.exams.models import Exam

    return Exam.objects.filter(
        id=int(exam_id),
        tenant=tenant,
        exam_type=Exam.ExamType.REGULAR,
        is_active=True,
        sessions__id=int(session_id),
    ).first()
