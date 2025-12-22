# apps/domains/submissions/models/submission_answer.py
from __future__ import annotations

from django.db import models
from apps.api.common.models import TimestampModel


class SubmissionAnswer(TimestampModel):
    """
    SubmissionAnswer = "답안 추출 중간산물"
    - 정답/점수/정오판정 금지 (results 도메인 책임)
    """
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="answers",
    )

    question_id = models.PositiveIntegerField()

    answer = models.TextField(blank=True)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["submission", "question_id"]),
        ]
        unique_together = ("submission", "question_id")

    def __str__(self) -> str:
        return f"SubmissionAnswer(submission={self.submission_id}, q={self.question_id})"
