from django.db import models
from apps.api.common.models import BaseModel
from .exam import Exam


class AnswerKey(BaseModel):
    """
    정답 정의 (값만 저장, 채점 로직 없음)
    """

    exam = models.OneToOneField(
        Exam,
        on_delete=models.CASCADE,
        related_name="answer_key",
    )

    # 예: { "1": "3", "2": "5", "3": "②" }
    answers = models.JSONField()

    class Meta:
        db_table = "exams_answer_key"

    def __str__(self):
        return f"AnswerKey for {self.exam.title}"
