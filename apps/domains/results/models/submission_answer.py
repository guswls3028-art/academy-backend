from django.db import models
from apps.api.common.models import BaseModel


class SubmissionAnswer(BaseModel):
    """
    results 도메인의 문항 단위 '채점 결과'

    🔥 SubmissionAnswer(submissions) = raw input
    🔥 SubmissionAnswer(results) = grading output (불변)
    """

    attempt = models.ForeignKey(
        "results.ExamAttempt",
        on_delete=models.CASCADE,
        related_name="answers",
    )

    question_id = models.PositiveIntegerField()

    detected = models.JSONField(default=list)      # ["B"]
    marking = models.CharField(max_length=20)      # single / multi / blank
    confidence = models.FloatField(default=0.0)
    status = models.CharField(
        max_length=20,
        help_text="ok / ambiguous / low_confidence / blank / error",
    )

    is_correct = models.BooleanField(null=True)
    score_awarded = models.FloatField(default=0.0)

    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "results_submission_answer"
        unique_together = ("attempt", "question_id")

    def __str__(self) -> str:
        return f"Attempt#{self.attempt_id} Q{self.question_id}"
