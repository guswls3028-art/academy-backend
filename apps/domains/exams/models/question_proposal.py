from __future__ import annotations

from django.db import models

from apps.api.common.models import BaseModel


class ExamQuestionProposal(BaseModel):
    """업로드 원본에서 분리했지만 교직원이 아직 확정하지 않은 문항 후보."""

    exam = models.ForeignKey(
        "exams.Exam",
        on_delete=models.CASCADE,
        related_name="question_proposals",
    )
    position = models.PositiveIntegerField()
    number = models.PositiveIntegerField()
    detected_number = models.PositiveIntegerField(null=True, blank=True)
    page_index = models.PositiveIntegerField(default=0)
    region_meta = models.JSONField(default=dict, blank=True)
    problem_image_key = models.CharField(max_length=500, blank=True, default="")
    explanation_text = models.TextField(blank=True, default="")
    explanation_image_key = models.CharField(max_length=500, blank=True, default="")
    match_confidence = models.FloatField(null=True, blank=True)
    problem_crop_ratio = models.FloatField(default=1.0)
    included = models.BooleanField(default=True)
    source_job_id = models.CharField(max_length=64, blank=True, default="")
    engine = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        db_table = "exams_question_proposal"
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["exam", "position"],
                name="exams_question_proposal_exam_position_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.exam_id}: proposal {self.position} -> Q{self.number}"
