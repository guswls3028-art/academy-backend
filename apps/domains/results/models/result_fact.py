# apps/domains/results/models/result_fact.py
from django.db import models
from apps.api.common.models import BaseModel


class ResultFact(BaseModel):
    """
    결과 Fact (append-only, 불변)
    - 집계/통계/이벤트 로그에 가까움

    ✅ attempt 중심 설계 반영
    - attempt_id: 이 Fact가 어느 attempt에서 나온 이벤트인지 추적 가능

    ⚠️ 리팩토링 메모 (중요)
    지금은 ResultFact가 answer/score/meta/source까지 들고 있음.
    장기적으로는:
      - ResultFact = "집계용 이벤트"
      - 상세/채점결과 = results.SubmissionAnswer 가 들고 가는 게 정석
    다만 지금 단계에서는 analytics 제거 + 단순 운영을 위해 유지.
    """

    target_type = models.CharField(max_length=20)
    target_id = models.PositiveIntegerField()

    enrollment_id = models.PositiveIntegerField()
    submission_id = models.PositiveIntegerField()

    # ✅ 어떤 attempt에서 생성된 Fact인지
    attempt_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="이 Fact를 생성한 ExamAttempt.id",
    )

    question_id = models.PositiveIntegerField()

    answer = models.TextField(blank=True)
    is_correct = models.BooleanField(default=False)

    score = models.FloatField(default=0.0)
    max_score = models.FloatField(default=0.0)

    source = models.CharField(max_length=20)
    meta = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "results_fact"
        ordering = ["-id"]
