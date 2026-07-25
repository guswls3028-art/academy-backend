# PATH: apps/core/models/program.py
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

from django.conf import settings
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
        ALL = "all", "전체 기능"

    PLAN_PRICES: dict[str, int] = {
        Plan.ALL: 145_000,
    }
    BILLING_MONTHLY_TAX_AMOUNT = 14_000
    BILLING_MONTHLY_TOTAL_AMOUNT = 159_000
    BILLING_VAT_RATE_PERCENT = None
    LEGACY_VAT_RATE_PERCENT = 10
    AUGUST_2026_PROMOTION_START = date(2026, 8, 1)
    AUGUST_2026_PROMOTION_END = date(2026, 8, 31)
    AUGUST_2026_PROMOTION_SUPPLY_AMOUNT = 145_000
    AUGUST_2026_PROMOTION_TAX_AMOUNT = 14_000
    AUGUST_2026_PROMOTION_TOTAL_AMOUNT = 159_000
    AUGUST_2026_PRICE_GUARANTEE_CODE = "august_2026_lifetime"
    AUGUST_2026_PRICE_GUARANTEE_LABEL = "2026년 8월 가입 평생 가격 보장"

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

    # ✅ 단일 요금제
    plan = models.CharField(
        max_length=20,
        choices=Plan.choices,
        default=Plan.ALL,
        help_text="단일 전체 기능 요금제",
    )
    monthly_price = models.PositiveIntegerField(
        default=145_000,
        help_text="월 공급가액(원). 단일 요금제 기준 145,000원.",
    )

    # ✅ 구독 관리
    # 해지 예약은 cancel_at_period_end 플래그로 관리.
    # subscription_status는 서비스 이용 가능 상태만 나타낸다.
    class SubscriptionStatus(models.TextChoices):
        ACTIVE = "active", "활성"
        EXPIRED = "expired", "만료"
        GRACE = "grace", "유예기간"

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

    # ✅ 결제 방식
    BILLING_MODE_CHOICES = [
        ("AUTO_CARD", "카드 자동결제"),
        ("INVOICE_REQUEST", "세금계산서 청구"),
    ]
    billing_mode = models.CharField(
        max_length=20,
        choices=BILLING_MODE_CHOICES,
        default="AUTO_CARD",
        help_text="결제 방식: 카드 자동결제 또는 세금계산서 청구",
    )
    next_billing_at = models.DateField(
        null=True, blank=True,
        help_text="다음 결제 예정일",
    )
    cancel_at_period_end = models.BooleanField(
        default=False,
        help_text="현재 구독 기간 종료 시 자동 해지",
    )
    canceled_at = models.DateTimeField(
        null=True, blank=True,
        help_text="해지 요청 시각",
    )

    # ✅ 법적 고지 정보 (이용약관, 개인정보처리방침에 표시)
    legal_company_name = models.CharField(
        max_length=200, blank=True, default="",
        help_text="상호 (법적 고지용)",
    )
    legal_representative = models.CharField(
        max_length=100, blank=True, default="",
        help_text="대표자명",
    )
    legal_business_number = models.CharField(
        max_length=50, blank=True, default="",
        help_text="사업자등록번호",
    )
    legal_ecommerce_number = models.CharField(
        max_length=100, blank=True, default="",
        help_text="통신판매업 신고번호",
    )
    legal_address = models.CharField(
        max_length=500, blank=True, default="",
        help_text="사업장 주소",
    )
    legal_support_email = models.CharField(
        max_length=200, blank=True, default="",
        help_text="고객센터 이메일",
    )
    legal_support_phone = models.CharField(
        max_length=50, blank=True, default="",
        help_text="고객센터 전화번호",
    )
    legal_privacy_officer_name = models.CharField(
        max_length=100, blank=True, default="",
        help_text="개인정보 보호책임자 성명",
    )
    legal_privacy_officer_contact = models.CharField(
        max_length=200, blank=True, default="",
        help_text="개인정보 보호책임자 연락처 (전화 또는 이메일)",
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
        # 신규 가입가는 생성 시 확정하고, 8월 평생 보장가만 이후 저장에서도
        # 강제한다. 기존 비보장 계약가는 명시적 rolling migration 전까지
        # 보존해 비가격 설정 저장이 가격 변경으로 번지지 않게 한다.
        normalized_fields: list[str] = []
        if self.plan != self.Plan.ALL:
            self.plan = self.Plan.ALL
            normalized_fields.append("plan")
        should_normalize_price = (
            self._state.adding or self.has_lifetime_price_guarantee
        )
        canonical_price = self.expected_monthly_price
        if should_normalize_price and self.monthly_price != canonical_price:
            self.monthly_price = canonical_price
            normalized_fields.append("monthly_price")
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and normalized_fields:
            kwargs["update_fields"] = list(
                dict.fromkeys([*update_fields, *normalized_fields])
            )
        super().save(*args, **kwargs)

    @classmethod
    def resolve_monthly_price(
        cls,
        *,
        plan: str,
        tenant_code: str | None = None,
        joined_on: date | None = None,
    ) -> int:
        if joined_on is not None and cls.is_august_2026_promotion_date(joined_on):
            return cls.AUGUST_2026_PROMOTION_SUPPLY_AMOUNT
        return cls.PLAN_PRICES[cls.Plan.ALL]

    @classmethod
    def is_august_2026_promotion_date(cls, joined_on: date) -> bool:
        return cls.AUGUST_2026_PROMOTION_START <= joined_on <= cls.AUGUST_2026_PROMOTION_END

    @property
    def joined_on(self) -> date:
        if self.created_at is None:
            return timezone.localdate()
        if timezone.is_naive(self.created_at):
            return self.created_at.date()
        return timezone.localtime(self.created_at).date()

    @property
    def has_lifetime_price_guarantee(self) -> bool:
        return self.is_august_2026_promotion_date(self.joined_on)

    @property
    def price_guarantee_code(self) -> str | None:
        if self.has_lifetime_price_guarantee:
            return self.AUGUST_2026_PRICE_GUARANTEE_CODE
        return None

    @property
    def price_guarantee_label(self) -> str | None:
        if self.has_lifetime_price_guarantee:
            return self.AUGUST_2026_PRICE_GUARANTEE_LABEL
        return None

    @property
    def expected_monthly_price(self) -> int:
        return self.resolve_monthly_price(
            plan=self.Plan.ALL,
            tenant_code=None,
            joined_on=self.joined_on,
        )

    @classmethod
    def calculate_monthly_amounts(cls, supply_amount: int) -> dict[str, int]:
        """Return a monthly amount breakdown.

        The active single-plan contract has an explicit 14,000 won tax amount.
        Other values are legacy invoice snapshots and retain the former 10%
        calculation for read-only compatibility.
        """
        if isinstance(supply_amount, bool) or not isinstance(supply_amount, int):
            raise TypeError("supply_amount must be an integer")
        if supply_amount <= 0:
            raise ValueError("supply_amount must be greater than zero")
        if supply_amount == cls.AUGUST_2026_PROMOTION_SUPPLY_AMOUNT:
            return {
                "supply_amount": supply_amount,
                "tax_amount": cls.AUGUST_2026_PROMOTION_TAX_AMOUNT,
                "total_amount": cls.AUGUST_2026_PROMOTION_TOTAL_AMOUNT,
            }
        tax_amount = supply_amount * cls.LEGACY_VAT_RATE_PERCENT // 100
        return {
            "supply_amount": supply_amount,
            "tax_amount": tax_amount,
            "total_amount": supply_amount + tax_amount,
        }

    @classmethod
    def aggregate_monthly_amounts(
        cls,
        programs: Iterable["Program"],
    ) -> dict[str, int]:
        totals = {
            "supply_amount": 0,
            "tax_amount": 0,
            "total_amount": 0,
        }
        for program in programs:
            amounts = program.monthly_amounts
            for key in totals:
                totals[key] += amounts[key]
        return totals

    @property
    def monthly_amounts(self) -> dict[str, int]:
        return self.calculate_monthly_amounts(self.monthly_price)

    @property
    def monthly_tax_amount(self) -> int:
        return self.monthly_amounts["tax_amount"]

    @property
    def monthly_total_amount(self) -> int:
        return self.monthly_amounts["total_amount"]

    @property
    def list_monthly_price(self) -> int:
        return self.PLAN_PRICES[self.Plan.ALL]

    @property
    def list_monthly_amounts(self) -> dict[str, int]:
        return self.calculate_monthly_amounts(self.list_monthly_price)

    @property
    def is_contract_price(self) -> bool:
        return self.has_lifetime_price_guarantee

    @property
    def billing_price_integrity(self) -> str:
        if self.plan != self.Plan.ALL:
            return "single_plan_mismatch"
        if self.monthly_price != self.expected_monthly_price:
            return "single_price_mismatch"
        return "ok"

    @property
    def is_billing_price_ready(self) -> bool:
        return self.billing_price_integrity == "ok"

    @property
    def billing_price_policy(self) -> str:
        if self.has_lifetime_price_guarantee:
            return "promotion"
        return "single"

    @property
    def billing_plan(self) -> str:
        return self.Plan.ALL

    @property
    def billing_plan_display(self) -> str:
        return self.Plan.ALL.label

    @property
    def billing_monthly_price(self) -> int:
        return self.monthly_price

    @property
    def monthly_discount_rate(self) -> int:
        if self.billing_price_policy != "promotion" or self.list_monthly_price <= 0:
            return 0
        return round((1 - self.monthly_price / self.list_monthly_price) * 100)

    @property
    def grace_period_days(self) -> int:
        grace_days = int(settings.BILLING_GRACE_PERIOD_DAYS)
        if grace_days < 0:
            raise ValueError("BILLING_GRACE_PERIOD_DAYS must not be negative")
        return grace_days

    @property
    def grace_expires_at(self):
        if (
            self.subscription_status != self.SubscriptionStatus.GRACE
            or self.subscription_expires_at is None
        ):
            return None
        return self.subscription_expires_at + timedelta(days=self.grace_period_days)

    @property
    def service_access_expires_at(self):
        """Last date on which service access is allowed for the current state."""
        if self.subscription_status == self.SubscriptionStatus.GRACE:
            return self.grace_expires_at
        return self.subscription_expires_at

    @property
    def is_subscription_active(self) -> bool:
        """구독이 유효한지 (활성 기간 또는 설정된 유예기간 이내)."""
        if self.tenant_id in set(
            getattr(settings, "BILLING_EXEMPT_TENANT_IDS", set()) or set()
        ):
            return True
        if self.subscription_status in (
            self.SubscriptionStatus.ACTIVE,
            self.SubscriptionStatus.GRACE,
        ):
            access_expires_at = self.service_access_expires_at
            if access_expires_at is None:
                return False
            return timezone.localdate() <= access_expires_at
        return False

    @property
    def days_remaining(self) -> int | None:
        """남은 실제 이용일수. grace 상태에서는 유예 종료일을 기준으로 한다."""
        access_expires_at = self.service_access_expires_at
        if access_expires_at is None:
            return None
        delta = (access_expires_at - timezone.localdate()).days
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
