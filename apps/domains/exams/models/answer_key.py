#apps/domains/exams/models/answer_key.py

from django.db import models
from apps.api.common.models import BaseModel
from .exam import Exam


class AnswerKey(BaseModel):
    """
    AnswerKey v2

    answers:
      {
        "123": "B",
        "124": "D"
      }
    key == ExamQuestion.id (string)
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

    def __str__(self):
        return f"AnswerKey v2 for {self.exam_id}"
