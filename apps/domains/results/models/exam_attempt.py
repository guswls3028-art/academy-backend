from django.db import models
from apps.api.common.models import BaseModel


class ExamAttempt(BaseModel):
    """
    학생의 '시험 1회 응시'를 나타내는 엔티티 (append-only)

    🔥 핵심 책임
    - Submission 단위가 아닌 '시험 응시 사실'의 고정
    - Result / Fact / Snapshot의 기준점
    """

    exam_id = models.PositiveIntegerField()
    enrollment_id = models.PositiveIntegerField()

    # Submission은 시도의 원인(event)
    submission_id = models.PositiveIntegerField(
        help_text="이 attempt를 발생시킨 submission"
    )

    attempt_index = models.PositiveIntegerField(help_text="1부터 시작")
    is_retake = models.BooleanField(default=False)

    # 서버가 판단하는 대표 attempt (Result는 항상 이것 기준)
    is_representative = models.BooleanField(default=True)

    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),     # 생성됨
            ("grading", "Grading"),     # 채점 중
            ("done", "Done"),           # 채점 완료
            ("failed", "Failed"),       # 채점 실패
        ],
        default="pending",
    )

    class Meta:
        db_table = "results_exam_attempt"
        unique_together = ("exam_id", "enrollment_id", "attempt_index")
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"ExamAttempt exam={self.exam_id} "
            f"enrollment={self.enrollment_id} "
            f"#{self.attempt_index}"
        )
