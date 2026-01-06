# apps/domains/submissions/models/submission_answer.py
from __future__ import annotations

from django.db import models
from apps.api.common.models import BaseModel


class SubmissionAnswer(BaseModel):
    """
    submissions 도메인의 문항 단위 raw 답안 (중간산물)

    ✅ FINAL CONTRACT (DO NOT BREAK)
    - exam_question_id = exams.ExamQuestion.id (NOT NULL)
    - number / question_id / legacy 개념 없음
    """

    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="answers",
    )

    # ✅ 단일 진실
    exam_question_id = models.PositiveIntegerField(
        db_index=True,
        help_text="Fixed contract: exams.ExamQuestion.id",
    )

    answer = models.TextField(blank=True)

    # meta는 submissions가 소유 (AI 원본/OMR 정보 저장)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "submissions_submissionanswer"

        indexes = [
            models.Index(fields=["exam_question_id"]),
            models.Index(fields=["submission", "exam_question_id"]),
        ]

        # ✅ 최종 고정
        unique_together = ("submission", "exam_question_id")

    def __str__(self):
        return f"Submission#{self.submission_id} Q={self.exam_question_id}"
