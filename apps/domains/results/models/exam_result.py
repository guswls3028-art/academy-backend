from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.api.common.models import BaseModel


class ExamResult(BaseModel):
    """
    results SSOT

    - submission 단위 결과 1개 (unique)
    - objective는 자동채점
    - subjective/descriptive는 수동채점(override)
    - finalized 되면 불변(운영 사고 차단)
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        FINAL = "FINAL", "Final"

    submission = models.OneToOneField(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="exam_result",
    )

    exam = models.ForeignKey(
        "exams.Exam",
        on_delete=models.CASCADE,
        related_name="results",
        help_text="regular exam",
    )

    # 점수
    max_score = models.FloatField(default=0.0)
    total_score = models.FloatField(default=0.0)
    objective_score = models.FloatField(default=0.0)
    subjective_score = models.FloatField(default=0.0)

    # 상태/확정
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    finalized_at = models.DateTimeField(null=True, blank=True)

    # 상세 내역 (프론트/디버깅/오답노트용)
    # 예: {"1": {"correct": true, "earned": 1, "answer": "A", "correct_answer": "A", "question_id": 123}, ...}
    breakdown = models.JSONField(default=dict, blank=True)

    # 수동 채점 override (번호 기준)
    # 예: {"6": {"earned": 2, "comment": "계산 실수"}, "10": {"earned": 0, "comment": "미제출"}}
    manual_overrides = models.JSONField(default=dict, blank=True)

    # pass/fail (계산 결과 저장해두면 UX가 쉬움)
    is_passed = models.BooleanField(default=False)

    class Meta:
        db_table = "results_exam_result"
        indexes = [
            models.Index(fields=["exam", "total_score"], name="results_exam_score_idx"),
            models.Index(
                fields=["exam", "status", "created_at"],
                name="results_exam_status_crted_idx",
            ),

        ]

    def finalize(self):
        self.status = self.Status.FINAL
        self.finalized_at = timezone.now()
        self.save(update_fields=["status", "finalized_at", "updated_at"])
