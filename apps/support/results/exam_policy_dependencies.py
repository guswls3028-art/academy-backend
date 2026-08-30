"""Cross-domain reads for lecture-specific exam operating policy."""

from __future__ import annotations

from typing import Any


def effective_exam_pass_score(*, exam: Any, lecture_id: int | None) -> float:
    from apps.domains.exams.services.lecture_policy_service import (
        effective_pass_score_for_exam,
    )

    return effective_pass_score_for_exam(exam=exam, lecture_id=lecture_id)


def effective_exam_pass_scores(
    *,
    exam: Any,
    lecture_ids: set[int],
) -> dict[int, float]:
    from apps.domains.exams.services.lecture_policy_service import (
        effective_pass_scores_for_exam,
    )

    return effective_pass_scores_for_exam(exam=exam, lecture_ids=lecture_ids)


def exam_pass_score_overrides(
    *,
    exam_ids: list[int] | set[int],
    lecture_id: int,
) -> dict[int, float]:
    from apps.domains.exams.models import ExamLecturePolicy

    return {
        int(row["exam_id"]): float(row["pass_score"])
        for row in ExamLecturePolicy.objects.filter(
            exam_id__in=exam_ids,
            lecture_id=int(lecture_id),
        ).values("exam_id", "pass_score")
    }


def enrollment_lecture_id(*, enrollment_id: int, tenant: Any) -> int | None:
    from apps.domains.enrollment.models import Enrollment

    value = Enrollment.objects.filter(
        id=int(enrollment_id),
        tenant=tenant,
    ).values_list("lecture_id", flat=True).first()
    return int(value) if value is not None else None


def linked_exam_lecture_ids(*, exam_id: int, tenant: Any) -> set[int]:
    from apps.domains.exams.models import Exam

    return {
        int(lecture_id)
        for lecture_id in Exam.objects.filter(
            id=int(exam_id),
            tenant=tenant,
            sessions__lecture__tenant=tenant,
        ).values_list("sessions__lecture_id", flat=True)
        if lecture_id is not None
    }


def exam_has_linked_lecture(
    *,
    exam_id: int,
    lecture_id: int,
    tenant: Any,
) -> bool:
    from apps.domains.exams.models import Exam

    return Exam.objects.filter(
        id=int(exam_id),
        tenant=tenant,
        sessions__lecture_id=int(lecture_id),
        sessions__lecture__tenant=tenant,
    ).exists()


def sessions_by_id_for_tenant(
    *,
    session_ids: list[int] | set[int],
    tenant: Any,
) -> dict[int, Any]:
    from apps.domains.lectures.models import Session

    return {
        int(session.id): session
        for session in Session.objects.filter(
            id__in=session_ids,
            lecture__tenant=tenant,
        )
    }
