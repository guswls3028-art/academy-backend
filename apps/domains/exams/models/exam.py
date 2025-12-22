from django.db import models
from apps.api.common.models import BaseModel


class Exam(BaseModel):
    """
    시험 정의 (메타 정보만)
    """

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    subject = models.CharField(max_length=100)

    # 예: 중간고사, 모의고사, 클리닉 테스트 등
    exam_type = models.CharField(
        max_length=50,
        default="regular",
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "exams_exam"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
