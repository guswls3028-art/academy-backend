# PATH: apps/domains/fees/models.py
"""
학원 수납 관리 시스템 (Student Fee Management)

학원(tenant)이 학생에게 수강료, 교재비 등을 청구하고 수납을 관리한다.
기존 billing 앱(플랫폼 구독 과금)과는 별개 도메인.
"""

from django.db import models
from django.utils import timezone

from apps.core.models.base import TimestampModel
from apps.core.db.tenant_queryset import TenantQuerySet


# ========================================================
# FeeTemplate (비목 정의)
# ========================================================

class FeeTemplate(TimestampModel):
    """
    학원의 비용 항목 정의 (가격표).
    강의에 연결하면 수강료, 연결 없으면 독립 비목(교재비 등).
    """
    objects = TenantQuerySet.as_manager()

    class FeeType(models.TextChoices):
        TUITION = "TUITION", "수강료"
        TEXTBOOK = "TEXTBOOK", "교재비"
        HANDOUT = "HANDOUT", "판서/프린트"
        REGISTRATION = "REGISTRATION", "등록비"
        MATERIAL = "MATERIAL", "재료비"
        OTHER = "OTHER", "기타"

    class BillingCycle(models.TextChoices):
        MONTHLY = "MONTHLY", "월납"
        ONE_TIME = "ONE_TIME", "일시납"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="fee_templates",
        db_index=True,
    )
    name = models.CharField(max_length=120, help_text="비목 이름")
    fee_type = models.CharField(max_length=20, choices=FeeType.choices)
    billing_cycle = models.CharField(
        max_length=20,
        choices=BillingCycle.choices,
        default=BillingCycle.MONTHLY,
    )
    amount = models.PositiveIntegerField(help_text="기본 금액 (원)")
    lecture = models.ForeignKey(
        "lectures.Lecture",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fee_templates",
        help_text="특정 강의에 연결 (null이면 독립 비목)",
    )
    auto_assign = models.BooleanField(
        default=True,
        help_text="수강 등록 시 자동 할당 여부 (False면 수동 할당만)",
    )
    is_active = models.BooleanField(default=True)
    memo = models.TextField(blank=True)

    class Meta:
        db_table = "fee_template"
        indexes = [
            models.Index(fields=["tenant", "fee_type"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "name"],
                name="uniq_fee_template_name_per_tenant",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_fee_type_display()}, {self.amount:,}원)"


# ========================================================
# StudentFee (학생별 비용 할당)
# ========================================================

class StudentFee(TimestampModel):
    """
    비목을 특정 학생에게 할당. 개별 조정(할인/감면) 지원.
    수강 등록 시 강의 연결 FeeTemplate이 있으면 자동 생성.
    """
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="student_fees",
        db_index=True,
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="student_fees",
    )
    fee_template = models.ForeignKey(
        FeeTemplate,
        on_delete=models.CASCADE,
        related_name="student_fees",
    )
    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_fees",
        help_text="수강료인 경우 수강 등록과 연결",
    )

    adjusted_amount = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="개별 조정 금액 (null이면 fee_template.amount 사용)",
    )
    discount_amount = models.PositiveIntegerField(
        default=0,
        help_text="할인 금액",
    )
    discount_reason = models.CharField(max_length=200, blank=True)

    # 청구 유효 기간 (null이면 무제한)
    billing_start_month = models.CharField(
        max_length=7, blank=True,
        help_text="청구 시작월 (YYYY-MM, 비어있으면 제한 없음)",
    )
    billing_end_month = models.CharField(
        max_length=7, blank=True,
        help_text="청구 종료월 (YYYY-MM, 비어있으면 제한 없음)",
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "student_fee"
        indexes = [
            models.Index(fields=["tenant", "student"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "student", "fee_template"],
                name="uniq_student_fee_per_template",
            ),
        ]

    @property
    def effective_amount(self) -> int:
        base = self.adjusted_amount if self.adjusted_amount is not None else self.fee_template.amount
        return max(0, base - self.discount_amount)

    def __str__(self):
        return f"{self.student.name} - {self.fee_template.name} ({self.effective_amount:,}원)"


# ========================================================
# StudentInvoice (청구서)
# ========================================================

STUDENT_INVOICE_STATUS_CHOICES = [
    ("PENDING", "미납"),
    ("PARTIAL", "부분납"),
    ("PAID", "완납"),
    ("OVERDUE", "연체"),
    ("CANCELLED", "취소"),
]


class StudentInvoice(TimestampModel):
    """
    학생별 월 청구서. 월 단위로 생성, 복수 항목 포함.
    paid_amount는 비정규화 필드 — service에서 select_for_update로 정합성 보장.
    """
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="student_invoices",
        db_index=True,
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="invoices",
    )

    invoice_number = models.CharField(
        max_length=40,
        help_text="청구번호 (예: FEE-2026-04-0001)",
    )
    billing_year = models.PositiveSmallIntegerField(help_text="청구 연도")
    billing_month = models.PositiveSmallIntegerField(help_text="청구 월 (1-12)")

    total_amount = models.PositiveIntegerField(help_text="청구 총액 (원)")
    paid_amount = models.PositiveIntegerField(default=0, help_text="납부 총액 (원)")

    status = models.CharField(
        max_length=20,
        choices=STUDENT_INVOICE_STATUS_CHOICES,
        default="PENDING",
    )
    due_date = models.DateField(help_text="납부 기한")
    paid_at = models.DateTimeField(null=True, blank=True, help_text="완납 일시")

    memo = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="청구서 생성 직원 (자동 생성 시 null)",
    )

    class Meta:
        db_table = "student_invoice"
        ordering = ["-billing_year", "-billing_month"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "student", "billing_year", "billing_month"]),
            models.Index(fields=["due_date"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "invoice_number"],
                name="uniq_student_invoice_number_per_tenant",
            ),
            models.UniqueConstraint(
                fields=["tenant", "student", "billing_year", "billing_month"],
                name="uniq_student_invoice_per_period",
            ),
        ]

    @property
    def outstanding_amount(self) -> int:
        return max(0, self.total_amount - self.paid_amount)

    def __str__(self):
        return f"{self.invoice_number} - {self.student.name} ({self.get_status_display()})"


# ========================================================
# InvoiceItem (청구 항목)
# ========================================================

class InvoiceItem(TimestampModel):
    """
    청구서의 개별 항목. 생성 시점의 비목명과 금액을 스냅샷으로 저장.
    """

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="invoice_items",
        db_index=True,
    )
    invoice = models.ForeignKey(
        StudentInvoice,
        on_delete=models.CASCADE,
        related_name="items",
    )
    fee_template = models.ForeignKey(
        FeeTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    description = models.CharField(max_length=200, help_text="항목명 (스냅샷)")
    amount = models.PositiveIntegerField(help_text="금액 (할인 반영 후)")

    class Meta:
        db_table = "student_invoice_item"
        indexes = [
            models.Index(fields=["tenant", "invoice"]),
        ]

    def __str__(self):
        return f"{self.description} - {self.amount:,}원"


# ========================================================
# FeePayment (수납 기록)
# ========================================================

FEE_PAYMENT_METHOD_CHOICES = [
    ("CARD", "카드"),
    ("BANK_TRANSFER", "계좌이체"),
    ("CASH", "현금"),
    ("OTHER", "기타"),
]

FEE_PAYMENT_STATUS_CHOICES = [
    ("SUCCESS", "완료"),
    ("CANCELLED", "취소"),
    ("REFUNDED", "환불"),
]


class FeePayment(TimestampModel):
    """
    청구서에 대한 실제 납부 기록.
    Phase 1에서는 관리자가 수동 기록 (현금/계좌이체/카드).
    """
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="fee_payments",
        db_index=True,
    )
    invoice = models.ForeignKey(
        StudentInvoice,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="fee_payments",
    )

    amount = models.PositiveIntegerField(help_text="납부 금액 (원)")
    payment_method = models.CharField(
        max_length=20,
        choices=FEE_PAYMENT_METHOD_CHOICES,
    )
    status = models.CharField(
        max_length=20,
        choices=FEE_PAYMENT_STATUS_CHOICES,
        default="SUCCESS",
    )

    paid_at = models.DateTimeField(help_text="납부 일시")
    recorded_by = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="수납 기록 직원",
    )
    receipt_note = models.CharField(
        max_length=300,
        blank=True,
        help_text="영수증 메모 / 입금자명",
    )
    memo = models.TextField(blank=True)

    class Meta:
        db_table = "fee_payment"
        ordering = ["-paid_at"]
        indexes = [
            models.Index(fields=["tenant", "student"]),
            models.Index(fields=["tenant", "invoice"]),
        ]

    def __str__(self):
        return f"{self.student.name} - {self.amount:,}원 ({self.get_payment_method_display()})"
