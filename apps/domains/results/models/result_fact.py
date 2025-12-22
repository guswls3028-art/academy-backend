from django.db import models
from apps.api.common.models import BaseModel


class ResultFact(BaseModel):
    """
    결과 Fact (append-only, 불변)
    """

    target_type = models.CharField(max_length=20)
    target_id = models.PositiveIntegerField()

    enrollment_id = models.PositiveIntegerField()
    submission_id = models.PositiveIntegerField()

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
