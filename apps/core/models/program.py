# PATH: apps/core/models/program.py
from __future__ import annotations

from django.db import models

from apps.core.models.base import TimestampModel
from apps.core.models.tenant import Tenant


class Program(TimestampModel):
    """
    Program (Tenant 1:1) — 원장 개인 프로그램 SSOT

    🔒 봉인 원칙:
    - Tenant 생성 시점에만 생성
    - read 경로에서 write 금지
    - 누락은 운영 데이터 무결성 위반
    """

    class LoginVariant(models.TextChoices):
        HAKWONPLUS = "hakwonplus", "HakwonPlus Admin"
        LIMGLISH = "limglish", "Limglish Teacher"
        CUSTOM = "custom", "Custom"

    class Plan(models.TextChoices):
        LITE = "lite", "Lite"
        BASIC = "basic", "Basic"
        PREMIUM = "premium", "Premium"

    PLAN_PRICES: dict[str, int] = {
        Plan.LITE: 99_000,
        Plan.BASIC: 150_000,
        Plan.PREMIUM: 300_000,
    }

    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name="program",
    )

    display_name = models.CharField(max_length=120, default="HakwonPlus")
    brand_key = models.CharField(
        max_length=80,
        default="hakwonplus",
        help_text="프론트 테마/리소스 로딩 키",
    )

    login_variant = models.CharField(
        max_length=30,
        choices=LoginVariant.choices,
        default=LoginVariant.HAKWONPLUS,
    )

    # ✅ 요금제 (리소스 계약 선언)
    plan = models.CharField(
        max_length=20,
        choices=Plan.choices,
        default=Plan.PREMIUM,
        help_text="요금제 (lite/basic/premium)",
    )
    monthly_price = models.PositiveIntegerField(
        default=300_000,
        help_text="월 요금(원). PLAN_PRICES 기준 자동 설정.",
    )

    feature_flags = models.JSONField(default=dict, blank=True)
    ui_config = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "core"
        indexes = [
            models.Index(fields=["tenant"]),
            models.Index(fields=["brand_key"]),
            models.Index(fields=["login_variant"]),
            models.Index(fields=["plan"]),
        ]

    def save(self, *args, **kwargs):
        # plan 변경 시 가격 자동 동기화
        if self.plan in self.PLAN_PRICES:
            self.monthly_price = self.PLAN_PRICES[self.plan]
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Program<{self.tenant.code}>:{self.display_name}"

    @classmethod
    def ensure_for_tenant(cls, *, tenant: Tenant) -> "Program":
        from academy.adapters.db.django import repositories_core as core_repo
        obj = core_repo.program_get_by_tenant(tenant)
        if obj:
            return obj
        raise RuntimeError(
            f"Program missing for tenant '{tenant.code}'. "
            "This violates core SSOT."
        )
