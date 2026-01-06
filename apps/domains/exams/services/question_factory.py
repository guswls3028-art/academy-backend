# apps/domains/exams/services/question_factory.py
from __future__ import annotations

from typing import List, Tuple

from django.db import transaction

from apps.domains.exams.models import Sheet, ExamQuestion

BBox = Tuple[int, int, int, int]  # (x, y, w, h)


@transaction.atomic
def create_questions_from_boxes(*, sheet_id: int, boxes: List[BBox]) -> List[ExamQuestion]:
    """
    Segmentation 결과(boxes)를 기반으로 ExamQuestion 자동 생성.

    설계 원칙 (중요):
    - idempotent: (sheet, number) 기준 update_or_create
    - boxes 개수 변화 시 기존 문제 삭제/추가 동기화
    - number = 시각적 순서 (1-based)
    - score는 이 단계에서 절대 건드리지 않음 (grading 책임)
    - bbox(region_meta)는 반드시 저장 (STEP 2 필수)

    ⚠️ 주의:
    - 이 함수는 '시험지 구조 정의'까지만 책임진다.
    - 채점 / 정답 비교 / 결과 생성은 results 도메인 책임.
    """

    sheet = Sheet.objects.select_for_update().get(id=int(sheet_id))

    # -------------------------------------------------
    # 1️⃣ total_questions 동기화
    # -------------------------------------------------
    total = int(len(boxes or []))
    if sheet.total_questions != total:
        sheet.total_questions = total
        sheet.save(update_fields=["total_questions", "updated_at"])

    # -------------------------------------------------
    # 2️⃣ 기존 문항 정리 (boxes 기준 동기화)
    # -------------------------------------------------
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

    # -------------------------------------------------
    # 3️⃣ 생성 / 갱신 (bbox 포함)
    # -------------------------------------------------
    created: List[ExamQuestion] = []

    for idx in range(1, total + 1):
        x, y, w, h = boxes[idx - 1]

        obj, _ = ExamQuestion.objects.update_or_create(
            sheet=sheet,
            number=idx,
            defaults={
                # 🔥 STEP 2 핵심
                "region_meta": {
                    "x": int(x),
                    "y": int(y),
                    "w": int(w),
                    "h": int(h),
                },
            },
        )
        created.append(obj)

    return created
