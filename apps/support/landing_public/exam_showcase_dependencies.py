"""Cross-domain dependencies for public exam showcase snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExamShowcaseSource:
    exam_title: str
    rows: list[dict[str, Any]]


def exam_showcase_source(*, tenant: Any, exam_id: int) -> ExamShowcaseSource:
    from apps.domains.exams.models import Exam
    from apps.domains.results.models import Result

    try:
        exam = Exam.objects.get(tenant=tenant, id=int(exam_id))
    except Exam.DoesNotExist:
        raise ValueError("시험이 없거나 권한이 없습니다.") from None

    qs = (
        Result.objects.filter(
            target_type="exam",
            target_id=exam.id,
            enrollment__student__tenant=tenant,
        )
        .select_related("enrollment", "enrollment__student")
    )

    rows: list[dict[str, Any]] = []
    for result in qs:
        if not result.enrollment or not result.enrollment.student:
            continue
        student = result.enrollment.student
        if (result.max_score or 0) <= 0:
            continue
        rows.append({
            "name": getattr(student, "name", "") or "",
            "phone": getattr(student, "phone", "") or "",
            "score": float(result.total_score or 0),
            "max_score": float(result.max_score or 0),
        })

    if not rows:
        raise ValueError("해당 시험의 채점 결과가 없습니다.")

    return ExamShowcaseSource(
        exam_title=exam.title if hasattr(exam, "title") else "",
        rows=rows,
    )
