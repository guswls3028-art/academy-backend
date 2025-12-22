from django.db import models
from apps.api.common.models import BaseModel


class ResultItem(BaseModel):
    """
    문항별 최신 결과 상태 (snapshot)
    """

    result = models.ForeignKey(
        "results.Result",
        on_delete=models.CASCADE,
        related_name="items",
    )

    question_id = models.PositiveIntegerField()

    answer = models.TextField(blank=True)
    is_correct = models.BooleanField(default=False)

    score = models.FloatField(default=0.0)
    max_score = models.FloatField(default=0.0)

    source = models.CharField(max_length=20)

    class Meta:
        db_table = "results_result_item"
        unique_together = ("result", "question_id")
