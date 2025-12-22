# apps/domains/exams/services/question_factory.py
from __future__ import annotations

from typing import List, Tuple

from django.db import transaction

from apps.domains.exams.models import Sheet, ExamQuestion

BBox = Tuple[int, int, int, int]


@transaction.atomic
def create_questions_from_boxes(*, sheet_id: int, boxes: List[BBox]) -> List[ExamQuestion]:
    """
    Segmentation 결과(boxes)를 기반으로 ExamQuestion 자동 생성.

    규칙:
    - idempotent: (sheet, number) 기준으로 update_or_create
    - boxes 개수 변화 시 기존 문제 삭제/추가 동기화
    - number = boxes 시각적 순서 (1-based)
    - score는 이 단계에서 건드리지 않음 (grading 단계 책임)
    """
    sheet = Sheet.objects.select_for_update().get(id=int(sheet_id))

    # 1️⃣ total_questions 동기화
    total = int(len(boxes or []))
    if sheet.total_questions != total:
        sheet.total_questions = total
        sheet.save(update_fields=["total_questions", "updated_at"])

    # 2️⃣ 기존 문제 정리
    existing_numbers = set(
        ExamQuestion.objects
        .filter(sheet=sheet)
        .values_list("number", flat=True)
    )
    new_numbers = set(range(1, total + 1))

    to_delete = existing_numbers - new_numbers
    if to_delete:
        ExamQuestion.objects.filter(
            sheet=sheet,
            number__in=to_delete,
        ).delete()

    # 3️⃣ 생성 / 갱신
    created: List[ExamQuestion] = []

    for idx in range(1, total + 1):
        obj, _ = ExamQuestion.objects.update_or_create(
            sheet=sheet,
            number=idx,
            defaults={},
        )
        created.append(obj)

    return created
