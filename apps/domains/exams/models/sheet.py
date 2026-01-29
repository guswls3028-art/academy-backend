# PATH: apps/domains/exams/models/sheet.py
from __future__ import annotations

from django.db import models
from apps.api.common.models import BaseModel
from .exam import Exam


class Sheet(BaseModel):
    """
    Sheet

    ✅ 확정 정책
    - Sheet는 template exam에만 귀속된다 (단일 진실)
    - 1 Exam : 1 Sheet (OneToOne)
    - regular exam은 sheet를 직접 가지지 않는다 (template을 통해 resolve)
    """

    exam = models.OneToOneField(
        Exam,
        on_delete=models.CASCADE,
        related_name="sheet",
    )

    name = models.CharField(max_length=50, default="MAIN")

    total_questions = models.PositiveIntegerField(default=0)

    file = models.FileField(
        upload_to="exams/sheets/",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "exams_sheet"

    def __str__(self) -> str:
        return f"{self.exam.title} - {self.name}"
