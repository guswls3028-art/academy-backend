# apps/domains/ai/models.py
from django.db import models
from django.utils import timezone
from apps.api.common.models import BaseModel


class AIJobModel(BaseModel):
    """
    API 서버가 관리하는 AI Job 메타 (DB가 SSOT)

    - 기존 필드 유지
    - 운영레벨: lease/visibility timeout/retry/idempotency 대응 필드 "추가"만
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

    payload = models.JSONField()
    error_message = models.TextField(blank=True)

    # ==================================================
    # ✅ ADD ONLY: 운영 레벨 필드 (DB Queue / lease 기반)
    # ==================================================
    tenant_id = models.CharField(max_length=64, null=True, blank=True)
    source_domain = models.CharField(max_length=64, null=True, blank=True)
    source_id = models.CharField(max_length=64, null=True, blank=True)

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


class AIResultModel(BaseModel):
    """
    AI 결과 fact (저장만, 계산 없음)

    - OneToOne 이므로 idempotency의 핵심 기반이 됨
    """
    job = models.OneToOneField(
        AIJobModel,
        on_delete=models.CASCADE,
        related_name="result",
    )
    payload = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "ai_result"
