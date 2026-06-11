# PATH: apps/domains/exams/models/answer_key.py
from __future__ import annotations

from django.db import models
from apps.api.common.models import BaseModel
from .exam import Exam


class AnswerKey(BaseModel):
    """
    AnswerKey

    ✅ 단일 진실:
    - template 또는 regular snapshot에 존재
    - legacy regular는 answer_key가 없으면 template_exam을 통해 resolve
    """

    exam = models.OneToOneField(
        Exam,
        on_delete=models.CASCADE,
        related_name="answer_key",
    )

    answers = models.JSONField(
        help_text="key=ExamQuestion.id (string), value=correct answer"
    )

    class Meta:
        db_table = "exams_answer_key"

    def __str__(self) -> str:
        return f"AnswerKey for template exam {self.exam_id}"
