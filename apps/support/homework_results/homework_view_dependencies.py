"""Cross-domain dependencies for homework result views."""

from __future__ import annotations

from typing import Any


def get_teacher_or_admin_permission() -> type:
    from apps.domains.results.permissions import IsTeacherOrAdmin

    return IsTeacherOrAdmin


def get_session_for_homework(*, session_id: int, tenant: Any, for_update: bool = False) -> Any | None:
    from apps.domains.lectures.models import Session

    queryset = Session.objects
    if for_update:
        queryset = queryset.select_for_update()
    return queryset.filter(id=session_id, lecture__tenant=tenant).first()


def delete_homework_assignments(*, tenant: Any, homework: Any) -> int:
    from apps.domains.homework.models import HomeworkAssignment

    deleted_count, _ = HomeworkAssignment.objects.filter(
        tenant=tenant,
        homework=homework,
    ).delete()
    return int(deleted_count)


def create_workbook_source_exam(*, tenant: Any, homework: Any) -> Any:
    from apps.domains.exams.models import Exam

    return Exam.objects.create(
        tenant=tenant,
        title=f"{homework.title} · 워크북 원본",
        description="과제 워크북의 문항·선생님 해설 원본",
        exam_type=Exam.ExamType.REGULAR,
        is_active=False,
        grading_mode=Exam.GradingMode.WRITTEN,
        manual_grading_method=Exam.ManualGradingMethod.CORRECTNESS,
        max_score=homework.default_max_score,
        student_results_published=False,
        answer_visibility=Exam.AnswerVisibility.HIDDEN,
    )


def workbook_source_none_status() -> str:
    from apps.domains.exams.models import Exam

    return str(Exam.SegmentationStatus.NONE)


def workbook_source_is_ready(source_exam: Any) -> bool:
    from apps.domains.exams.models import Exam

    return (
        source_exam is not None
        and source_exam.segmentation_status == Exam.SegmentationStatus.READY
    )


def homework_assignments_for_question_grading(*, homework: Any) -> list[Any]:
    from apps.domains.homework.models import HomeworkAssignment

    return list(
        HomeworkAssignment.objects.filter(
            tenant_id=homework.tenant_id,
            homework=homework,
            session_id=homework.session_id,
        )
        .select_related("enrollment__student")
        .order_by("enrollment__student__name", "enrollment_id")
    )


def homework_assignment_enrollment_ids(*, homework: Any) -> set[int]:
    from apps.domains.homework.models import HomeworkAssignment

    return set(
        HomeworkAssignment.objects.filter(
            tenant_id=homework.tenant_id,
            homework=homework,
            session_id=homework.session_id,
        ).values_list("enrollment_id", flat=True)
    )


def get_homework_raw_score_cutline(
    *,
    session: Any,
    homework: Any | None = None,
) -> float | None:
    from apps.domains.homework.models import HomeworkPolicy

    if homework is not None:
        homework_mode = getattr(homework, "cutline_mode", None)
        homework_value = getattr(homework, "cutline_value", None)
        if homework_mode is not None and homework_value is not None:
            return float(homework_value) if homework_mode == "COUNT" else None

    policy = HomeworkPolicy.objects.filter(
        tenant_id=session.lecture.tenant_id,
        session=session,
    ).first()
    if policy is None or policy.cutline_mode != HomeworkPolicy.CutlineMode.COUNT:
        return None
    return float(policy.cutline_value)


def resolve_homework_cutline_settings(
    *,
    homework: Any,
    create_policy: bool = False,
) -> Any:
    from apps.domains.homework.utils.homework_policy import (
        resolve_homework_cutline_settings as resolve,
    )

    return resolve(
        session=homework.session,
        homework=homework,
        create_policy=create_policy,
    )
