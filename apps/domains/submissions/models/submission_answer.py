# PATH: apps/domains/submissions/models/submission_answer.py
from __future__ import annotations

from django.db import models
from apps.api.common.models import BaseModel
from apps.core.models import Tenant


class SubmissionAnswer(BaseModel):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="submission_answers",
    )

    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="answers",
    )

    exam_question_id = models.PositiveIntegerField(
        db_index=True,
        help_text="Fixed contract: exams.ExamQuestion.id",
    )

    answer = models.TextField(blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "submissions_submissionanswer"
        indexes = [
            models.Index(fields=["exam_question_id"]),
            models.Index(fields=["submission", "exam_question_id"]),
        ]
        unique_together = ("submission", "exam_question_id")

    def __str__(self):
        return f"Submission#{self.submission_id} Q={self.exam_question_id}"
