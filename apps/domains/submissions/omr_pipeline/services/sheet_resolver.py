"""
OMR Sheet 조회 — tenant + exam scope 강제.

답안 좌표는 정답키와 같은 등급의 민감 데이터다. 다른 시험의 Sheet 로
fallback 하면 버블이 잘못된 문항에 매핑된다. 이 모듈은 tenant 검증 +
effective_template_exam_id 검증을 무조건 통과한 Sheet 만 반환한다.

이전: apps/domains/submissions/services/dispatcher.py 안에 박혀 있어서
        dispatcher 책임(orchestration / SQS / EC2)과 섞여 있었다. dispatcher 의
        호환을 위해 같은 이름으로 re-export.
"""
from __future__ import annotations

from typing import Optional

from apps.domains.exams.models import Sheet
from apps.domains.submissions.models import Submission


def resolve_omr_sheet_for_exam(
    *,
    tenant,
    exam_id: int,
    requested_sheet_id: Optional[int],
) -> Sheet:
    """
    Resolve the OMR sheet for an exam with tenant/exam scoping.

    Fail closed when:
    - exam 이 tenant 와 일치하지 않거나 존재하지 않음.
    - requested_sheet_id 가 이 exam 의 sheet 가 아님.
    - 어느 Sheet 도 매칭되지 않음.
    """
    from apps.domains.exams.models import Exam

    exam = Exam.objects.filter(id=int(exam_id), tenant=tenant).first()
    if not exam:
        raise ValueError("OMR target exam not found for tenant")

    allowed_exam_ids = {int(exam.id), int(exam.effective_template_exam_id)}
    qs = Sheet.objects.select_related("exam").filter(
        exam_id__in=allowed_exam_ids,
        exam__tenant=tenant,
    )

    if requested_sheet_id:
        sheet = qs.filter(id=int(requested_sheet_id)).first()
        if not sheet:
            raise ValueError("sheet_id does not belong to this exam")
        return sheet

    preferred = qs.filter(exam_id=int(exam.effective_template_exam_id)).first()
    sheet = preferred or qs.first()
    if not sheet:
        raise ValueError("OMR sheet not found for this exam")
    return sheet


def resolve_omr_sheet_for_submission(
    submission: Submission,
    requested_sheet_id: Optional[int],
) -> Sheet:
    if submission.target_type != Submission.TargetType.EXAM:
        raise ValueError("OMR submission target_type must be exam")
    return resolve_omr_sheet_for_exam(
        tenant=submission.tenant,
        exam_id=int(submission.target_id),
        requested_sheet_id=requested_sheet_id,
    )
