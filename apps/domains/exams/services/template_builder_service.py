# PATH: apps/domains/exams/services/template_builder_service.py
from __future__ import annotations

from typing import Dict, Any

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Exam, Sheet, AnswerKey
from apps.domains.exams.services.template_resolver import assert_template_editable


class TemplateBuilderService:
    """
    Template Builder Service (SSOT)

    책임:
    - 템플릿 시험의 '생성 직후 필수 구조'를 보장
    - 프론트/운영이 신뢰할 수 있는 최소 상태를 강제

    보장 사항:
    - template exam은 반드시 Sheet(1:1)를 가진다
    - AnswerKey는 반드시 존재한다 (빈 answers라도)
    - total_questions는 항상 Sheet 기준으로 신뢰 가능
    """

    @staticmethod
    @transaction.atomic
    def ensure_initialized(template_exam: Exam) -> Dict[str, Any]:
        if template_exam.exam_type != Exam.ExamType.TEMPLATE:
            raise ValidationError({"detail": "template exam required"})

        # 이미 운영 시험에서 사용 중이면 구조 변경 불가
        assert_template_editable(template_exam)

        # 1️⃣ Sheet 보장 (1:1)
        sheet, _ = Sheet.objects.get_or_create(
            exam=template_exam,
            defaults={
                "name": "MAIN",
                "total_questions": 0,
            },
        )

        # 2️⃣ AnswerKey 보장 (빈 answers 허용)
        answer_key, _ = AnswerKey.objects.get_or_create(
            exam=template_exam,
            defaults={
                "answers": {},
            },
        )

        return {
            "exam_id": template_exam.id,
            "sheet_id": sheet.id,
            "answer_key_id": answer_key.id,
            "total_questions": sheet.total_questions,
        }
