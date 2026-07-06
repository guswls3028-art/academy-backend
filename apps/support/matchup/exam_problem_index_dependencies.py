"""Cross-domain dependencies for matchup exam-problem indexing."""

from __future__ import annotations

from typing import Any


def template_exam_problem_rows(*, tenant_id: int | None) -> list[tuple[int, int, str, int]]:
    from apps.domains.exams.models import Exam

    qs = Exam.objects.filter(
        exam_type=Exam.ExamType.TEMPLATE,
        sheet__isnull=False,
        sheet__total_questions__gt=0,
    ).select_related("sheet")
    if tenant_id:
        qs = qs.filter(tenant_id=tenant_id)
    return list(qs.values_list("id", "tenant_id", "title", "sheet__total_questions"))


def dispatch_matchup_index_exam_job(*, exam_id: int, tenant_id: int) -> Any:
    from apps.domains.ai.gateway import dispatch_job

    return dispatch_job(
        job_type="matchup_index_exam",
        payload={
            "exam_id": str(exam_id),
            "tenant_id": str(tenant_id),
        },
        tenant_id=str(tenant_id),
        source_domain="matchup_index",
        source_id=str(exam_id),
    )
