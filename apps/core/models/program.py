# PATH: apps/core/models/program.py
from __future__ import annotations

from datetime import date

from django.db import models
from django.utils import timezone

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
        STANDARD = "standard", "Standard"
        PRO = "pro", "Pro"
        MAX = "max", "Max"

    PLAN_PRICES: dict[str, int] = {
        Plan.STANDARD: 99_000,
        Plan.PRO: 198_000,
        Plan.MAX: 300_000,
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
        default=Plan.PRO,
        help_text="요금제 (standard/pro/max)",
    )
    monthly_price = models.PositiveIntegerField(
        default=198_000,
        help_text="월 요금(원). PLAN_PRICES 기준 자동 설정.",
    )

    # ✅ 구독 관리
    class SubscriptionStatus(models.TextChoices):
        ACTIVE = "active", "활성"
        EXPIRED = "expired", "만료"
        GRACE = "grace", "유예기간"
        CANCELLED = "cancelled", "해지"

    subscription_status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.ACTIVE,
        db_index=True,
        help_text="구독 상태",
    )
    subscription_started_at = models.DateField(
        null=True,
        blank=True,
        help_text="구독 시작일",
    )
    subscription_expires_at = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text="구독 만료일 (이 날까지 이용 가능)",
    )
    billing_email = models.EmailField(
        max_length=254,
        blank=True,
        default="",
        help_text="결제 관련 이메일 알림 수신",
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
        # plan 변경 시 가격 자동 동기화 (프로모션 가격이 설정되어 있으면 유지)
        if self.plan in self.PLAN_PRICES:
            # monthly_price가 아직 기본값이거나 다른 플랜의 정가인 경우에만 동기화
            other_plan_prices = {v for k, v in self.PLAN_PRICES.items() if k != self.plan}
            if self.monthly_price in other_plan_prices or self.monthly_price == 0:
                self.monthly_price = self.PLAN_PRICES[self.plan]
                uf = kwargs.get("update_fields")
                if uf is not None and "monthly_price" not in uf:
                    kwargs["update_fields"] = list(uf) + ["monthly_price"]
        super().save(*args, **kwargs)

    @property
    def is_subscription_active(self) -> bool:
        """구독이 유효한지 (활성 or 유예기간 + 만료일 미도래)"""
        if self.subscription_status in (self.SubscriptionStatus.ACTIVE, self.SubscriptionStatus.GRACE):
            if self.subscription_expires_at is None:
                return True  # 만료일 미설정 = 무제한
            return date.today() <= self.subscription_expires_at
        return False

    @property
    def days_remaining(self) -> int | None:
        """남은 이용일수 (만료일 없으면 None)"""
        if self.subscription_expires_at is None:
            return None
        delta = (self.subscription_expires_at - date.today()).days
        return max(0, delta)

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
