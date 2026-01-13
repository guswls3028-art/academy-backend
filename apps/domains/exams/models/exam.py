# apps/domains/exams/models/exam.py
from django.db import models
from apps.api.common.models import BaseModel
from apps.domains.lectures.models import Session


class Exam(BaseModel):
    """
    시험 정의 (메타 정보 + 운영 정책)
    """

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    subject = models.CharField(max_length=100)

    exam_type = models.CharField(max_length=50, default="regular")
    is_active = models.BooleanField(default=True)

    # ===============================
    # 🔥 핵심: Session : Exam = N:M
    # ===============================
    sessions = models.ManyToManyField(
        Session,
        related_name="exams",
        blank=True,
        help_text="이 시험이 속한 차시들",
    )

    allow_retake = models.BooleanField(default=False)
    max_attempts = models.PositiveIntegerField(default=1)
    pass_score = models.FloatField(default=0.0)

    open_at = models.DateTimeField(null=True, blank=True)
    close_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "exams_exam"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
