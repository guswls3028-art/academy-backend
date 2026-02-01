# PATH: apps/domains/results/guards/grading_contract.py
from __future__ import annotations

from typing import Dict

from django.core.exceptions import ValidationError

from apps.domains.exams.models import Exam
from apps.domains.exams.models.sheet import Sheet
from apps.domains.exams.models.answer_key import AnswerKey


class GradingContractGuard:
    """
    Boundary guard for grading.

    목적:
    - 채점 로직 이전에 SSOT 정합성 검증
    - 런타임 import 에러 / 조용한 오답 생성 방지
    - 워커/동기 호출 공통 보호막
    """

    @staticmethod
    def validate_exam_for_grading(exam: Exam) -> tuple[Sheet, AnswerKey]:
        # REGULAR exam만 채점 가능
        if exam.exam_type != Exam.ExamType.REGULAR:
            raise ValidationError("only REGULAR exams are gradable")

        if not exam.template_exam_id:
            raise ValidationError("regular exam must have template_exam")

        template_exam = exam.template_exam

        # Sheet 검증
        sheet = getattr(template_exam, "sheet", None)
        if not isinstance(sheet, Sheet):
            raise ValidationError("template exam must have a valid sheet")

        # AnswerKey 검증
        answer_key = getattr(template_exam, "answer_key", None)
        if not isinstance(answer_key, AnswerKey):
            raise ValidationError("template exam must have an answer_key")

        if not isinstance(answer_key.answers, dict):
            raise ValidationError("answer_key.answers must be a dict")

        # Question–AnswerKey 정합성 (존재 여부만)
        question_ids = {int(q.id) for q in sheet.questions.all()}
        key_ids = {
            int(k)
            for k in answer_key.answers.keys()
            if isinstance(k, (int, str)) and str(k).isdigit()
        }

        if not key_ids.issubset(question_ids):
            raise ValidationError("answer_key contains unknown question ids")

        return sheet, answer_key
