"""Cross-domain exam policy reads used by progress services."""

from __future__ import annotations

from typing import Any


def exam_by_id(*, exam_id: int, tenant_id: int | None = None) -> Any | None:
    from apps.domains.exams.models import Exam

    filters: dict[str, Any] = {"id": int(exam_id)}
    if tenant_id is not None:
        filters["tenant_id"] = int(tenant_id)
    return Exam.objects.filter(**filters).first()


def effective_exam_pass_score(*, exam: Any, lecture_id: int | None) -> float:
    from apps.domains.exams.services.lecture_policy_service import (
        effective_pass_score_for_exam,
    )

    return effective_pass_score_for_exam(exam=exam, lecture_id=lecture_id)


def exam_pass_score_overrides(
    *,
    exam_ids: list[int],
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
