# PATH: apps/domains/staffs/models.py
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError

from apps.api.common.models import TimestampModel


class Staff(TimestampModel):
    """
    조교 / 아르바이트생
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_profile",
    )

    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True)

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

    def __str__(self) -> str:
        return self.name


class WorkType(TimestampModel):
    """
    근무 유형
    """

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
    조교별 근무유형/시급
    """

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
        help_text="비우면 WorkType 기본 시급",
    )

    class Meta:
        unique_together = ("staff", "work_type")

    @property
    def effective_hourly_wage(self) -> int:
        return self.hourly_wage or self.work_type.base_hourly_wage

    def __str__(self) -> str:
        return f"{self.staff.name} - {self.work_type.name}"


class WorkRecord(TimestampModel):
    """
    출퇴근 기록
    """

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
    end_time = models.TimeField()
    break_minutes = models.PositiveIntegerField(default=0)

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

    memo = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-start_time"]

    def _calculate_hours_and_amount(self):
        start_dt = datetime.combine(self.date, self.start_time)
        end_dt = datetime.combine(self.date, self.end_time)
        if end_dt < start_dt:
            end_dt += timedelta(days=1)

        total_minutes = (end_dt - start_dt).total_seconds() / 60
        total_minutes = max(0, total_minutes - self.break_minutes)
        hours = Decimal(total_minutes / 60).quantize(Decimal("0.01"))

        try:
            swt = StaffWorkType.objects.get(
                staff=self.staff,
                work_type=self.work_type,
            )
            wage = swt.effective_hourly_wage
        except StaffWorkType.DoesNotExist:
            wage = self.work_type.base_hourly_wage

        amount = int(hours * Decimal(wage))
        return hours, amount

    def save(self, *args, **kwargs):
        if self.work_hours is None or self.amount is None:
            self.work_hours, self.amount = self._calculate_hours_and_amount()
        super().save(*args, **kwargs)


class ExpenseRecord(TimestampModel):
    """
    기타 비용 (승인 워크플로우 포함)
    """

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
        unique_together = ("staff", "year", "month")
        ordering = ["-year", "-month"]

    def __str__(self):
        return f"{self.staff.name} - {self.year}-{self.month:02d}"


class PayrollSnapshot(TimestampModel):
    """
    월별 급여 정산 스냅샷 (불변)
    """

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
        unique_together = ("staff", "year", "month")
        ordering = ["-year", "-month"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("PayrollSnapshot은 수정할 수 없습니다.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.staff.name} {self.year}-{self.month:02d}"
