# apps/domains/ai/models.py
from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.core.models.base import BaseModel, TimestampModel


class AIJobModel(BaseModel):
    """
    AI Job Meta (DB is SSOT)
    - API server owns lifecycle
    - Worker pulls via internal endpoints
    """

    job_id = models.CharField(max_length=64, unique=True)
    job_type = models.CharField(max_length=50)

    status = models.CharField(
        max_length=32,
        choices=[
            ("PENDING", "PENDING"),
            ("VALIDATING", "VALIDATING"),
            ("RUNNING", "RUNNING"),
            ("DONE", "DONE"),
            ("FAILED", "FAILED"),
            ("REJECTED_BAD_INPUT", "REJECTED_BAD_INPUT"),
            ("FALLBACK_TO_GPU", "FALLBACK_TO_GPU"),
            ("RETRYING", "RETRYING"),
            ("REVIEW_REQUIRED", "REVIEW_REQUIRED"),
        ],
        default="PENDING",
        db_index=True,
    )

    payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")

    # ---- routing / trace ----
    tenant_id = models.CharField(max_length=64, null=True, blank=True)
    source_domain = models.CharField(max_length=64, null=True, blank=True)
    source_id = models.CharField(max_length=64, null=True, blank=True)
    
    # ---- tier routing ----
    tier = models.CharField(
        max_length=20,
        choices=[
            ("lite", "Lite"),
            ("basic", "Basic"),
            ("premium", "Premium"),
        ],
        default="basic",
        db_index=True,
        help_text="Tier determines queue routing and processing capabilities",
    )

    # ---- retry / lease ----
    attempt_count = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=5)

    locked_by = models.CharField(max_length=128, null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    next_run_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True, default="")

    # ---- idempotency (Phase 0 안정성) ----
    idempotency_key = models.CharField(
        max_length=256,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="tenant_id:exam_id:student_id:job_type:file_hash, 중복 요청 방지",
    )
    force_rerun = models.BooleanField(default=False)
    rerun_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ai_job"
        indexes = [
            models.Index(fields=["status", "next_run_at"], name="ai_job_status_next_run_idx"),
            models.Index(fields=["lease_expires_at"], name="ai_job_lease_idx"),
            models.Index(fields=["source_domain", "source_id"], name="ai_job_source_idx"),
            models.Index(fields=["tier", "status", "next_run_at"], name="ai_job_tier_stat_next_idx"),
        ]

    def __str__(self) -> str:
        return f"AIJobModel<{self.job_id}>({self.job_type})[{self.tier}][{self.status}]"


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


class TenantConfigModel(BaseModel):
    """
    학원별 AI 설정 (GPU Fallback 등).
    Phase 0에서 선택 사용, 없으면 기본값.
    """

    tenant_id = models.CharField(max_length=64, unique=True, db_index=True)

    has_premium_subscription = models.BooleanField(default=False)
    allow_gpu_fallback = models.BooleanField(default=False)
    gpu_fallback_threshold = models.FloatField(default=0.5)

    class Meta:
        db_table = "ai_tenant_config"

    def __str__(self) -> str:
        return f"TenantConfig(tenant_id={self.tenant_id})"


class AIRuntimeConfigModel(BaseModel):
    """
    전역 런타임 플래그 (배포 없이 ON/OFF).
    예: ai_shadow_mode → REVIEW Shadow Mode
    """

    key = models.CharField(max_length=128, unique=True, db_index=True)
    value = models.CharField(max_length=512, blank=True)

    class Meta:
        db_table = "ai_runtime_config"

    def __str__(self) -> str:
        return f"AIRuntimeConfig({self.key}={self.value})"


# ──────────────────────────────────────────────────────────────────────
# AI Usage Quota (테넌트별 AI 호출 카운트 — 비용 폭증 방지)
# ──────────────────────────────────────────────────────────────────────

class AIUsageModel(TimestampModel):
    """테넌트 × kind × (year, month) 단위 AI 호출 카운트.

    quota service가 select_for_update로 잠그고 증가시킨다.
    가격정책 결정 전 default 한도(`apps.domains.ai.services.quota.DEFAULT_LIMITS`)
    적용 → 한도 초과 시 AIQuotaExceeded 예외.
    """

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="ai_usages",
        db_index=True,
    )
    kind = models.CharField(
        max_length=32,
        db_index=True,
        help_text="matchup / ocr / embedding / problem_generation / schema_infer",
    )
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    day = models.PositiveSmallIntegerField(
        help_text="일일 한도 추적용. 0이면 월간 집계 row.",
        default=0,
    )
    count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "ai_usage"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "kind", "year", "month", "day"],
                name="uniq_ai_usage_per_period",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "kind", "year", "month"]),
        ]

    def __str__(self) -> str:
        return f"AIUsage({self.tenant_id}, {self.kind}, {self.year}-{self.month:02d}-{self.day:02d}={self.count})"
