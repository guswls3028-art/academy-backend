# apps/domains/exams/models/sheet.py
from django.db import models
from apps.api.common.models import BaseModel
from .exam import Exam


class Sheet(BaseModel):
    """
    시험지 (A형, B형 등)
    """

    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name="sheets",
    )

    name = models.CharField(max_length=50)  # A형, B형

    # ✅ 자동 생성 서비스가 동기화하므로 default=0이 맞음
    total_questions = models.PositiveIntegerField(default=0)

    # 시험지 원본 이미지 / PDF
    file = models.FileField(
        upload_to="exams/sheets/",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "exams_sheet"
        unique_together = ("exam", "name")

    def __str__(self):
        return f"{self.exam.title} - {self.name}"
