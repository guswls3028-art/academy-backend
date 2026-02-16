# PATH: apps/domains/staffs/models.py
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError

from apps.api.common.models import TimestampModel
from apps.core.models import Tenant
from apps.core.db import TenantQuerySet


# ======================================================
# Payroll Calculation Policies (Enterprise Level)
# ======================================================

class WorkHourCalculationPolicy:
    """
    근무 시간 계산 정책
    - 시간 왜곡 금지
    - 휴게시간은 분 단위로 차감
    """

    @staticmethod
    def calculate(date, start_time, end_time, break_minutes) -> Decimal:
        start_dt = datetime.combine(date, start_time)
        end_dt = datetime.combine(date, end_time)
        if end_dt < start_dt:
            end_dt += timedelta(days=1)

        total_minutes = (end_dt - start_dt).total_seconds() / 60
        total_minutes = max(0, total_minutes - break_minutes)

        return Decimal(total_minutes / 60).quantize(Decimal("0.01"))


class WageResolutionPolicy:
    """
    단가 결정 정책
    """

    @staticmethod
    def resolve(*, tenant, staff, work_type) -> int:
        from academy.adapters.db.django import repositories_staffs as staff_repo

        swt = staff_repo.staff_work_type_get_or_none(tenant=tenant, staff=staff, work_type=work_type)
        if swt:
            return swt.effective_hourly_wage
        return work_type.base_hourly_wage


class PayrollAmountPolicy:
    """
    금액 계산 정책
    """

    @staticmethod
    def calculate(hours: Decimal, hourly_wage: int) -> int:
        return int(hours * Decimal(hourly_wage))


# ======================================================
# Domain Models
# ======================================================

class Staff(TimestampModel):
    """
    직원 / 강사
    """

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="staffs",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_profile",
    )

    name = models.CharField(max_length=100)
    phone = models.CharField(
        max_length=20,
        blank=True,
        help_text="정규화된 전화번호 (하이픈 제거, 예: 01012345678)",
    )

    is_active = models.BooleanField(default=True)
    is_manager = models.BooleanField(default=False)

    PAY_TYPE_CHOICES = (
        ("HOURLY", "시급"),
        ("MONTHLY", "월급"),
    )
    pay_type = models.CharField(
        max_length=20,
        choices=PAY_TYPE_CHOICES,
        default="HOURLY",
    )

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "created_at"]),  # ✅ 복합 인덱스 추가
        ]
        constraints = [
            # ✅ tenant 단위 전화번호 유일성 (phone이 있는 경우만)
            models.UniqueConstraint(
                fields=["tenant", "phone"],
                condition=models.Q(phone__isnull=False) & ~models.Q(phone=""),
                name="uniq_staff_phone_per_tenant",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class WorkType(TimestampModel):
    """
    급여 블록
    """

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="work_types",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    name = models.CharField(max_length=100)
    base_hourly_wage = models.PositiveIntegerField(default=0)

    color = models.CharField(
        max_length=7,
        default="#4CAF50",
        help_text="HEX 색상 코드",
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class StaffWorkType(TimestampModel):
    """
    Staff ↔ WorkType 연결
    """

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="staff_work_types",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="staff_work_types",
    )
    work_type = models.ForeignKey(
        WorkType,
        on_delete=models.CASCADE,
        related_name="staff_work_types",
    )

    hourly_wage = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="비우면 WorkType 기본 단가",
    )

    class Meta:
        unique_together = ("tenant", "staff", "work_type")

    @property
    def effective_hourly_wage(self) -> int:
        return self.hourly_wage or self.work_type.base_hourly_wage

    def __str__(self) -> str:
        return f"{self.staff.name} - {self.work_type.name}"


class WorkRecord(TimestampModel):
    """
    근무 사실(Fact)
    """

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="work_records",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="work_records",
    )
    work_type = models.ForeignKey(
        WorkType,
        on_delete=models.PROTECT,
        related_name="work_records",
    )

    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField(null=True, blank=True)
    break_minutes = models.PositiveIntegerField(default=0)

    # ✅ 원본에 있었던 필드 복구
    current_break_started_at = models.DateTimeField(null=True, blank=True)

    work_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    amount = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    # ✅ 원본에 있었던 필드 복구
    resolved_hourly_wage = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="급여 계산에 실제 사용된 시급",
    )

    memo = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-start_time"]
        indexes = [
            models.Index(fields=["tenant", "date"]),  # ✅ 복합 인덱스 추가
        ]

    def calculate_payroll(self):
        hours = WorkHourCalculationPolicy.calculate(
            self.date,
            self.start_time,
            self.end_time,
            self.break_minutes,
        )

        wage = WageResolutionPolicy.resolve(
            tenant=self.tenant,
            staff=self.staff,
            work_type=self.work_type,
        )

        amount = PayrollAmountPolicy.calculate(hours, wage)
        return hours, amount, wage

    def save(self, *args, **kwargs):
        if self.end_time and (self.work_hours is None or self.amount is None):
            self.work_hours, self.amount, self.resolved_hourly_wage = self.calculate_payroll()
        super().save(*args, **kwargs)


# ======================================================
# ↓↓↓ 원본에 있었던 하단 모델들 복구 ↓↓↓
# ======================================================

class ExpenseRecord(TimestampModel):
    """
    기타 비용 (승인 워크플로우 포함)
    """

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="expense_records",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="expense_records",
    )

    date = models.DateField()
    title = models.CharField(max_length=255)
    amount = models.PositiveIntegerField()
    memo = models.TextField(blank=True)

    STATUS_CHOICES = (
        ("PENDING", "대기"),
        ("APPROVED", "승인"),
        ("REJECTED", "반려"),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
    )

    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_expenses",
    )

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.staff.name} - {self.title}"


class WorkMonthLock(TimestampModel):
    """
    근무 월 마감
    """

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="work_month_locks",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="work_month_locks",
    )
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField()
    is_locked = models.BooleanField(default=True)

    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="locked_work_months",
    )

    class Meta:
        unique_together = ("tenant", "staff", "year", "month")
        ordering = ["-year", "-month"]

    def __str__(self):
        return f"{self.staff.name} - {self.year}-{self.month:02d}"


class PayrollSnapshot(TimestampModel):
    """
    월별 급여 정산 스냅샷 (불변)
    """

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="payroll_snapshots",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    staff = models.ForeignKey(
        Staff,
        on_delete=models.CASCADE,
        related_name="payroll_snapshots",
    )

    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField()

    work_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    work_amount = models.PositiveIntegerField(default=0)
    approved_expense_amount = models.PositiveIntegerField(default=0)
    total_amount = models.PositiveIntegerField(default=0)

    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generated_payroll_snapshots",
    )

    class Meta:
        unique_together = ("tenant", "staff", "year", "month")
        ordering = ["-year", "-month"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("PayrollSnapshot은 수정할 수 없습니다.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.staff.name} {self.year}-{self.month:02d}"
