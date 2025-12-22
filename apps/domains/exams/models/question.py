from django.db import models
from apps.api.common.models import BaseModel
from .sheet import Sheet


class ExamQuestion(BaseModel):
    """
    시험 문항 정의
    """

    sheet = models.ForeignKey(
        Sheet,
        on_delete=models.CASCADE,
        related_name="questions",
    )

    number = models.PositiveIntegerField()  # 1번, 2번 ...
    score = models.FloatField(default=1.0)

    # 문항 이미지 (AI로 잘라낸 결과 포함 가능)
    image = models.ImageField(
        upload_to="exams/questions/",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "exams_question"
        unique_together = ("sheet", "number")
        ordering = ["number"]

    def __str__(self):
        return f"{self.sheet} Q{self.number}"
