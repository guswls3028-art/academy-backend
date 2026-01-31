# PATH: apps/domains/exams/services/template_validation_service.py
from __future__ import annotations

from typing import Dict, Any

from apps.domains.exams.models import Exam, ExamQuestion, AnswerKey


class TemplateValidationService:
    """
    Template Validation Service

    책임:
    - 템플릿이 실제 시험(regular)로 생성 가능한지 판단
    - 프론트 추측 금지, 서버 단 판단만 신뢰
    """

    @staticmethod
    def validate_for_regular(template_exam: Exam) -> Dict[str, Any]:
        sheet = getattr(template_exam, "sheet", None)
        if not sheet:
            return {"ok": False, "reason": "SHEET_MISSING"}

        q_count = (
            ExamQuestion.objects
            .filter(sheet=sheet)
            .count()
        )
        if q_count <= 0:
            return {"ok": False, "reason": "NO_QUESTIONS"}

        if not AnswerKey.objects.filter(exam=template_exam).exists():
            return {"ok": False, "reason": "ANSWER_KEY_MISSING"}

        return {"ok": True}
