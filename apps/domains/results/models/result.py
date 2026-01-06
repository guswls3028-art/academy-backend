# apps/domains/results/models/result.py
from django.db import models
from apps.api.common.models import BaseModel


class Result(BaseModel):
    """
    시험/숙제 결과 최신 스냅샷 (조회용)
    계산 없음

    ✅ attempt 중심 설계 반영
    - attempt_id: 이 Result가 어떤 ExamAttempt(시도)를 대표하는지 추적 가능
    - 재시험/대표 attempt 교체 시에도 "어떤 attempt 결과인지" 명확해짐

    ⚠️ 주의:
    - 기존 데이터가 있으면 attempt_id는 일단 NULL 허용으로 들어감 (마이그레이션에서 null=True)
    - 운영에서 백필 후 null=False로 tighten 하는 2단계가 정석
    """

    target_type = models.CharField(max_length=20)  # exam / homework
    target_id = models.PositiveIntegerField()

    enrollment_id = models.PositiveIntegerField()

    # ✅ 어떤 attempt의 결과인지 추적 (대표 attempt 기준)
    attempt_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="이 Result가 참조하는 대표 ExamAttempt.id",
    )

    total_score = models.FloatField(default=0.0)
    max_score = models.FloatField(default=0.0)

    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "results_result"
        unique_together = ("target_type", "target_id", "enrollment_id")
