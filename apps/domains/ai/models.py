# apps/domains/ai/models.py
from django.db import models
from apps.api.common.models import BaseModel


class AIJobModel(BaseModel):
    """
    API 서버가 관리하는 AI Job 메타
    (DB-based Queue + Lock + Retry)
    """
    job_id = models.CharField(max_length=64, unique=True)
    job_type = models.CharField(max_length=50)

    status = models.CharField(
        max_length=20,
        choices=[
            ("PENDING", "PENDING"),
            ("RUNNING", "RUNNING"),
            ("DONE", "DONE"),
            ("FAILED", "FAILED"),
        ],
        default="PENDING",
        db_index=True,
    )

    payload = models.JSONField()
    error_message = models.TextField(blank=True)

    # =========================
    # 🔒 Queue / Retry / Lock
    # =========================
    retry_count = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=5)

    locked_by = models.CharField(max_length=100, null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_job"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["locked_at"]),
        ]


class AIResultModel(BaseModel):
    """
    AI 결과 fact (저장만, 계산 없음)
    """
    job = models.OneToOneField(
        AIJobModel,
        on_delete=models.CASCADE,
        related_name="result",
    )
    payload = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "ai_result"
