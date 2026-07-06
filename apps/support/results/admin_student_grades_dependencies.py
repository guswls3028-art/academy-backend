"""Cross-domain read dependencies for admin student grades."""

from __future__ import annotations

from typing import Any

from django.db.models import Max


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
    for exam in Exam.objects.filter(id__in=exam_ids, tenant=tenant).only("id", "title", "pass_score"):
        exams_map[exam.id] = {
            "title": exam.title,
            "pass_score": float(exam.pass_score or 0),
        }
    return exams_map


def enrollment_lecture_metadata_by_id(*, tenant: Any, enrollment_ids: list[int]) -> dict[int, dict[str, Any]]:
    from apps.domains.enrollment.models import Enrollment

    enrollment_lecture_map = {}
    enrollments = (
        Enrollment.objects.filter(id__in=enrollment_ids, tenant=tenant)
        .select_related("lecture")
        .only(
            "id",
            "lecture__id",
            "lecture__title",
            "lecture__color",
            "lecture__chip_label",
        )
    )
    for enrollment in enrollments:
        enrollment_lecture_map[enrollment.id] = {
            "lecture_id": enrollment.lecture_id,
            "lecture_title": enrollment.lecture.title if enrollment.lecture else None,
            "lecture_color": getattr(enrollment.lecture, "color", None),
            "lecture_chip_label": getattr(enrollment.lecture, "chip_label", None),
        }
    return enrollment_lecture_map


def homework_scores_for_grades(enrollment_ids: list[int]):
    from apps.domains.homework_results.models import HomeworkScore

    return (
        HomeworkScore.objects.filter(enrollment_id__in=enrollment_ids, attempt_index=1)
        .exclude(score__isnull=True)
        .exclude(session__lecture__is_system=True)
        .select_related("homework", "session", "session__lecture")
        .order_by("-updated_at")
    )


def resolved_homework_link_types(
    *,
    enrollment_ids: list[int],
    homework_ids: list[int],
) -> dict[tuple[int, int], str]:
    from apps.domains.progress.models import ClinicLink

    links = {}
    for link in ClinicLink.objects.filter(
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
    enrollment_ids: list[int],
    homework_ids: list[int],
) -> dict[tuple[int, int], int]:
    from apps.domains.homework_results.models import HomeworkScore

    counts = {}
    for row in (
        HomeworkScore.objects.filter(
            homework_id__in=homework_ids,
            enrollment_id__in=enrollment_ids,
        )
        .values("homework_id", "enrollment_id")
        .annotate(max_attempt=Max("attempt_index"))
    ):
        counts[(row["enrollment_id"], row["homework_id"])] = row["max_attempt"]
    return counts
