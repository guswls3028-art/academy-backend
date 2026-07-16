"""Cross-domain read dependencies for admin student grades."""

from __future__ import annotations

from typing import Any

from django.db.models import Max, Prefetch


def active_student_for_grades(*, tenant: Any, student_id: int) -> Any | None:
    from apps.domains.students.selectors import active_student_by_id

    return active_student_by_id(tenant, student_id)


def enrollment_ids_for_student(*, tenant: Any, student_id: int) -> list[int]:
    from apps.domains.enrollment.models import Enrollment

    return list(
        Enrollment.objects.filter(
            student_id=student_id,
            tenant=tenant,
        ).values_list("id", flat=True)
    )


def exam_metadata_by_id(*, tenant: Any, exam_ids: list[int]) -> dict[int, dict[str, Any]]:
    from apps.domains.exams.models import Exam

    exams_map = {}
    exams = (
        Exam.objects.filter(
            id__in=exam_ids,
            tenant=tenant,
            exam_type=Exam.ExamType.REGULAR,
        )
        .select_related("sheet", "template_exam")
        .only(
            "id",
            "title",
            "pass_score",
            "is_active",
            "exam_type",
            "template_exam_id",
            "template_exam__tenant_id",
            "sheet__id",
        )
    )
    for exam in exams:
        structure_exam_id = exam.effective_structure_exam_id
        if (
            structure_exam_id != int(exam.id)
            and getattr(exam.template_exam, "tenant_id", None) != tenant.id
        ):
            # A corrupt cross-tenant template FK must never authorize reading
            # that template's questions. Falling back to the local exam id is
            # fail-closed for legacy regular exams without their own sheet.
            structure_exam_id = int(exam.id)
        exams_map[exam.id] = {
            "title": exam.title,
            "pass_score": float(exam.pass_score or 0),
            "is_active": bool(exam.is_active),
            "effective_structure_exam_id": structure_exam_id,
        }
    return exams_map


def primary_session_metadata_by_exam_and_lecture(
    *,
    tenant: Any,
    exam_lecture_pairs: set[tuple[int, int]],
) -> dict[tuple[int, int], dict[str, Any]]:
    """Return the first matching session for each ``(exam, lecture)`` pair.

    Archived exams remain part of a student's score history, so this read does
    not require ``is_active=True``.  Scoping by the result enrollment's lecture
    prevents a multi-lecture exam from borrowing another lecture's metadata or
    system visibility policy.  The two-query prefetch replaces the old per-exam
    ``exists()`` + ``first()`` + lazy lecture lookup chain.
    """
    if not exam_lecture_pairs:
        return {}

    from apps.domains.exams.models import Exam
    from apps.domains.lectures.models import Session

    exam_ids = {exam_id for exam_id, _lecture_id in exam_lecture_pairs}
    lecture_ids = {lecture_id for _exam_id, lecture_id in exam_lecture_pairs}
    tenant_exams = Exam.objects.filter(
        id__in=exam_ids,
        tenant=tenant,
        exam_type=Exam.ExamType.REGULAR,
    ).only("id")
    sessions = (
        Session.objects.filter(
            lecture__tenant=tenant,
            lecture_id__in=lecture_ids,
            exams__in=tenant_exams,
        )
        .select_related("lecture")
        .prefetch_related(
            Prefetch(
                "exams",
                queryset=tenant_exams,
                to_attr="_student_grade_exams",
            )
        )
        .distinct()
        .order_by("lecture_id", "order", "id")
    )

    metadata: dict[tuple[int, int], dict[str, Any]] = {}
    for session in sessions:
        lecture = session.lecture
        row = {
            "session_id": session.id,
            "session_title": session.title or session.display_label,
            "session_order": int(session.order),
            "session_regular_order": (
                int(session.regular_order)
                if session.regular_order is not None
                else None
            ),
            "session_date": session.date,
            "lecture_id": session.lecture_id,
            "lecture_title": lecture.title if lecture else None,
            "lecture_color": getattr(lecture, "color", None),
            "lecture_chip_label": getattr(lecture, "chip_label", None),
            "lecture_is_system": bool(getattr(lecture, "is_system", False)),
        }
        for exam in session._student_grade_exams:
            key = (int(exam.id), int(session.lecture_id))
            if key in exam_lecture_pairs:
                metadata.setdefault(key, row)
    return metadata


def enrollment_lecture_metadata_by_id(*, tenant: Any, enrollment_ids: list[int]) -> dict[int, dict[str, Any]]:
    from apps.domains.enrollment.models import Enrollment

    enrollment_lecture_map = {}
    enrollments = (
        Enrollment.objects.filter(
            id__in=enrollment_ids,
            tenant=tenant,
            lecture__tenant=tenant,
        )
        .select_related("lecture")
        .only(
            "id",
            "lecture__id",
            "lecture__title",
            "lecture__color",
            "lecture__chip_label",
            "lecture__is_system",
        )
    )
    for enrollment in enrollments:
        enrollment_lecture_map[enrollment.id] = {
            "lecture_id": enrollment.lecture_id,
            "lecture_title": enrollment.lecture.title if enrollment.lecture else None,
            "lecture_color": getattr(enrollment.lecture, "color", None),
            "lecture_chip_label": getattr(enrollment.lecture, "chip_label", None),
            "lecture_is_system": bool(getattr(enrollment.lecture, "is_system", False)),
        }
    return enrollment_lecture_map


def homework_scores_for_grades(*, tenant: Any, enrollment_ids: list[int]):
    from apps.domains.homework_results.models import HomeworkScore

    return (
        HomeworkScore.objects.filter(
            enrollment_id__in=enrollment_ids,
            enrollment__tenant=tenant,
            homework__tenant=tenant,
            session__lecture__tenant=tenant,
            attempt_index=1,
        )
        .exclude(score__isnull=True)
        .exclude(session__lecture__is_system=True)
        .select_related("homework", "session", "session__lecture")
        .order_by("-updated_at")
    )


def resolved_homework_link_types(
    *,
    tenant: Any,
    enrollment_ids: list[int],
    homework_ids: list[int],
) -> dict[tuple[int, int], str]:
    from apps.domains.progress.models import ClinicLink

    links = {}
    for link in ClinicLink.objects.filter(
        tenant=tenant,
        enrollment_id__in=enrollment_ids,
        source_type="homework",
        source_id__in=homework_ids,
        resolved_at__isnull=False,
        resolution_type__in=["EXAM_PASS", "HOMEWORK_PASS", "MANUAL_OVERRIDE"],
    ).values("enrollment_id", "source_id", "resolution_type"):
        links[(link["enrollment_id"], link["source_id"])] = link["resolution_type"]
    return links


def homework_retake_counts_by_key(
    *,
    tenant: Any,
    enrollment_ids: list[int],
    homework_ids: list[int],
) -> dict[tuple[int, int], int]:
    from apps.domains.homework_results.models import HomeworkScore

    counts = {}
    for row in (
        HomeworkScore.objects.filter(
            homework_id__in=homework_ids,
            enrollment_id__in=enrollment_ids,
            enrollment__tenant=tenant,
            homework__tenant=tenant,
            session__lecture__tenant=tenant,
        )
        .exclude(session__lecture__is_system=True)
        .values("homework_id", "enrollment_id")
        .annotate(max_attempt=Max("attempt_index"))
    ):
        counts[(row["enrollment_id"], row["homework_id"])] = row["max_attempt"]
    return counts
