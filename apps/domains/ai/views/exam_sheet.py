# PATH: apps/domains/exams/models/exam_sheet.py
from django.db import models


class ExamSheet(models.Model):
    """
    시험(exam)에서 사용하는 시험지(sheet)
    - 시험 실행 시 단일 진실
    """

    exam_id = models.PositiveIntegerField(db_index=True)
    sheet_id = models.PositiveIntegerField(db_index=True)

    class Meta:
        db_table = "exams_exam_sheet"
        unique_together = ("exam_id", "sheet_id")
