# PATH: apps/core/models/ops_audit.py
"""
플랫폼 운영(/dev) 콘솔 감사 로그.

dev_app에서 발생한 모든 변경(테넌트 생성/수정, owner 등록/제거,
maintenance 토글, billing 연장/플랜변경/입금처리, 임퍼소네이션, 인박스 답변 등)을 기록.
"""
from django.conf import settings
from django.db import models

from .base import TimestampModel


class OpsAuditLog(TimestampModel):
    """플랫폼 운영 작업 감사 로그."""

    class Result(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    # 누가
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    actor_username = models.CharField(max_length=150, blank=True, default="")

    # 무엇을 (tenant.create, owner.register, maintenance.toggle, billing.extend ...)
    action = models.CharField(max_length=64, db_index=True)
    summary = models.CharField(max_length=255, blank=True, default="")

    # 대상
    target_tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # 디테일
    payload = models.JSONField(default=dict, blank=True)
    result = models.CharField(
        max_length=16,
        choices=Result.choices,
        default=Result.SUCCESS,
    )
    error = models.CharField(max_length=255, blank=True, default="")

    # 컨텍스트
    ip = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "ops_audit_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"], name="ops_audit_l_created_idx"),
            models.Index(fields=["action", "-created_at"], name="ops_audit_l_action_idx"),
            models.Index(fields=["target_tenant", "-created_at"], name="ops_audit_l_tenant_idx"),
            models.Index(fields=["actor_user", "-created_at"], name="ops_audit_l_actor_idx"),
        ]

    def __str__(self) -> str:
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.actor_username} :: {self.action}"
