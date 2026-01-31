# apps/domains/ai/models.py
from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.api.common.models import BaseModel


class AIJobModel(BaseModel):
    """
    AI Job Meta (DB is SSOT)
    - API server owns lifecycle
    - Worker pulls via internal endpoints
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
    )

    payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")

    # ---- routing / trace ----
    tenant_id = models.CharField(max_length=64, null=True, blank=True)
    source_domain = models.CharField(max_length=64, null=True, blank=True)
    source_id = models.CharField(max_length=64, null=True, blank=True)

    # ---- retry / lease ----
    attempt_count = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=5)

    locked_by = models.CharField(max_length=128, null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)

    next_run_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ai_job"
        indexes = [
            models.Index(fields=["status", "next_run_at"], name="ai_job_status_next_run_idx"),
            models.Index(fields=["lease_expires_at"], name="ai_job_lease_idx"),
            models.Index(fields=["source_domain", "source_id"], name="ai_job_source_idx"),
        ]

    def __str__(self) -> str:
        return f"AIJobModel<{self.job_id}>({self.job_type})[{self.status}]"


class AIResultModel(BaseModel):
    """
    AI Result Fact (write-once, idempotency anchor)
    - OneToOne to enforce single fact row per job
    """

    job = models.OneToOneField(
        AIJobModel,
        on_delete=models.CASCADE,
        related_name="result",
    )
    payload = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "ai_result"

    def __str__(self) -> str:
        return f"AIResultModel(job_id={self.job_id})"
