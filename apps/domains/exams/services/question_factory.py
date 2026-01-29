# apps/domains/exams/services/question_factory.py
from __future__ import annotations

from typing import List, Tuple

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Sheet, ExamQuestion, Exam
from apps.domains.exams.services.template_resolver import assert_template_editable

BBox = Tuple[int, int, int, int]  # (x, y, w, h)


@transaction.atomic
def create_questions_from_boxes(*, sheet_id: int, boxes: List[BBox]) -> List[ExamQuestion]:
    """
    Segmentation 결과(boxes)를 기반으로 ExamQuestion 자동 생성.

    봉인 규칙:
    - template exam의 sheet에서만 수행 가능
    - template이 이미 regular에 의해 참조 중이면 구조 변경 금지
    - idempotent: (sheet, number) update_or_create
    - boxes 개수 변화 시 기존 문제 삭제/추가 동기화
    - score는 절대 건드리지 않음
    - bbox(region_meta) 저장 필수
    """

    sheet = (
        Sheet.objects
        .select_for_update()
        .select_related("exam")
        .get(id=int(sheet_id))
    )

    exam: Exam = sheet.exam
    if exam.exam_type != Exam.ExamType.TEMPLATE:
        raise ValidationError({"detail": "Questions can be auto-generated only for template exams."})

    # template이 regular에 의해 사용 중이면 구조 봉인
    assert_template_editable(exam)

    total = int(len(boxes or []))

    # 1) total_questions 동기화
    if sheet.total_questions != total:
        sheet.total_questions = total
        sheet.save(update_fields=["total_questions", "updated_at"])

    # 2) 기존 문항 정리
    existing_numbers = set(
        ExamQuestion.objects.filter(sheet=sheet).values_list("number", flat=True)
    )
    new_numbers = set(range(1, total + 1))

    to_delete = existing_numbers - new_numbers
    if to_delete:
        ExamQuestion.objects.filter(sheet=sheet, number__in=to_delete).delete()

    # 3) 생성/갱신
    created: List[ExamQuestion] = []
    for idx in range(1, total + 1):
        x, y, w, h = boxes[idx - 1]

        obj, _ = ExamQuestion.objects.update_or_create(
            sheet=sheet,
            number=idx,
            defaults={
                "region_meta": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)},
            },
        )
        created.append(obj)

    return created
