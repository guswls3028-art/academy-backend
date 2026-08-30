from __future__ import annotations

from django.db import models
from django.db.models import Q

from apps.api.common.models import BaseModel


class ExamLecturePolicy(BaseModel):
    """Lecture-specific operating policy for one shared regular exam."""

    exam = models.ForeignKey(
        "exams.Exam",
        on_delete=models.CASCADE,
        related_name="lecture_policies",
    )
    lecture = models.ForeignKey(
        "lectures.Lecture",
        on_delete=models.CASCADE,
        related_name="exam_policies",
    )
    pass_score = models.FloatField(
        help_text="이 강의에서 적용할 합격·귀가 기준 점수",
    )

    class Meta:
        db_table = "exams_exam_lecture_policy"
        constraints = [
            models.UniqueConstraint(
                fields=["exam", "lecture"],
                name="uniq_exam_lecture_policy",
            ),
            models.CheckConstraint(
                condition=Q(pass_score__gte=0),
                name="exam_lecture_pass_score_gte_zero",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"ExamLecturePolicy(exam={self.exam_id}, "
            f"lecture={self.lecture_id}, pass_score={self.pass_score})"
        )
