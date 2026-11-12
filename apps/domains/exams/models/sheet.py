# PATH: apps/domains/exams/models/sheet.py
from __future__ import annotations

from django.db import models
from apps.api.common.models import BaseModel
from .exam import Exam


class Sheet(BaseModel):
    """
    Sheet

    ✅ 확정 정책
    - Sheet는 template 또는 regular snapshot에 귀속된다 (단일 진실)
    - 1 Exam : 1 Sheet (OneToOne)
    - legacy regular는 sheet가 없으면 template을 통해 resolve한다
    """

    exam = models.OneToOneField(
        Exam,
        on_delete=models.CASCADE,
        related_name="sheet",
    )

    name = models.CharField(max_length=50, default="MAIN")

    total_questions = models.PositiveIntegerField(default=0)

    choice_count = models.PositiveIntegerField(default=0)

    essay_count = models.PositiveIntegerField(default=0)

    # Beta: print a teacher score strip (tens/ones/tenths) in each essay row and let
    # the OMR worker read it as a grading-grid proposal. Owner: docs/domain/omr.md.
    essay_score_marks = models.BooleanField(
        default=False,
        db_default=False,
        help_text="서술형 칸마다 교사 점수 마킹칸을 인쇄하고 스캔에서 읽는다(Beta).",
    )

    file = models.FileField(
        upload_to="exams/sheets/",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "exams_sheet"

    def __str__(self) -> str:
        return f"{self.exam.title} - {self.name}"
