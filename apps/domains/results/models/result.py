from django.db import models
from apps.api.common.models import BaseModel


class Result(BaseModel):
    """
    시험/숙제 결과 최신 스냅샷 (조회용)
    계산 없음
    """

    target_type = models.CharField(max_length=20)  # exam / homework
    target_id = models.PositiveIntegerField()

    enrollment_id = models.PositiveIntegerField()

    total_score = models.FloatField(default=0.0)
    max_score = models.FloatField(default=0.0)

    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "results_result"
        unique_together = ("target_type", "target_id", "enrollment_id")
