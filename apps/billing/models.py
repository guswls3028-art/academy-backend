"""
결제/구독 도메인 모델

상태 분리 원칙:
- subscription status: Program.subscription_status (ACTIVE/EXPIRED/GRACE)
  해지 예약은 cancel_at_period_end 플래그로 별도 관리.
- invoice status: Invoice.status (SCHEDULED/PENDING/PAID/FAILED/OVERDUE/VOID)
- payment status: PaymentTransaction.status (PENDING/SUCCESS/FAILED/REFUNDED)
- tax invoice status: TaxInvoiceIssue.status (NOT_REQUESTED/REQUESTED/READY/ISSUED/FAILED)
"""

import uuid

from django.db import models

from apps.core.models.base import TimestampModel

INVOICE_STATUS_CHOICES = [
    ("SCHEDULED", "예정"),
    ("PENDING", "결제 대기"),
    ("PAID", "결제 완료"),
    ("FAILED", "결제 실패"),
    ("OVERDUE", "연체"),
    ("VOID", "무효"),
]

PAYMENT_STATUS_CHOICES = [
    ("PENDING", "처리 중"),
    ("SUCCESS", "성공"),
    ("FAILED", "실패"),
    ("REFUNDED", "환불"),
    ("PARTIALLY_REFUNDED", "부분 환불"),
]

TAX_INVOICE_STATUS_CHOICES = [
    ("NOT_REQUESTED", "미요청"),
    ("REQUESTED", "발행 요청"),
    ("READY", "발행 준비"),
    ("ISSUED", "발행 완료"),
    ("FAILED", "발행 실패"),
]


class BillingProfile(TimestampModel):
    """카드 자동결제 프로필 — 테넌트당 1개"""

    tenant = models.OneToOneField(
        "core.Tenant", on_delete=models.CASCADE, related_name="billing_profile"
    )
    provider = models.CharField(
        max_length=30, default="tosspayments", help_text="PG사"
    )
    provider_customer_key = models.CharField(
        max_length=200, blank=True,
        help_text="PG사 고객 고유 키 (예측 불가능한 UUID, 자동 생성)",
    )
    payer_name = models.CharField(
        max_length=100, blank=True, help_text="결제자 이름"
    )
    payer_email = models.EmailField(blank=True, help_text="결제자 이메일")
    payer_phone = models.CharField(
        max_length=20, blank=True, help_text="결제자 전화번호"
    )

    class Meta:
        db_table = "billing_profile"
        verbose_name = "결제 프로필"
        verbose_name_plural = "결제 프로필"

    def save(self, *args, **kwargs):
        if not self.provider_customer_key:
            self.provider_customer_key = f"cus_{uuid.uuid4().hex}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"BillingProfile({self.tenant.code})"


class BillingKey(TimestampModel):
    """카드 빌링키 — 자동결제용 토큰화된 카드 정보"""

    tenant = models.ForeignKey(
        "core.Tenant", on_delete=models.CASCADE, related_name="billing_keys"
    )
    billing_profile = models.ForeignKey(
        BillingProfile, on_delete=models.CASCADE, related_name="keys"
    )
    provider = models.CharField(max_length=30, default="tosspayments")
    billing_key = models.CharField(
        max_length=200, help_text="PG사 빌링키 (암호화 저장 권장)"
    )
    card_company = models.CharField(
        max_length=50, blank=True, help_text="카드사 (예: 삼성, 현대)"
    )
    card_number_masked = models.CharField(
        max_length=20, blank=True, help_text="마스킹된 카드번호 (예: **** 1234)"
    )
    is_active = models.BooleanField(default=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "billing_key"
        verbose_name = "빌링키"
        verbose_name_plural = "빌링키"
        indexes = [
            models.Index(fields=["tenant", "is_active"]),
        ]
        constraints = [
            # tenant당 활성 빌링키 1개만 허용 — application-level select_for_update
            # 외 defense in depth. service.issue_billing_key 우회 경로 차단.
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_active=True),
                name="billingkey_one_active_per_tenant",
            ),
        ]

    def __str__(self):
        return f"BillingKey({self.tenant.code}, {self.card_number_masked})"


class BusinessProfile(TimestampModel):
    """사업자 정보 — 세금계산서 발행용"""

    tenant = models.OneToOneField(
        "core.Tenant", on_delete=models.CASCADE, related_name="business_profile"
    )
    business_name = models.CharField(max_length=200, help_text="상호")
    representative_name = models.CharField(max_length=100, help_text="대표자명")
    business_registration_number = models.CharField(
        max_length=12, help_text="사업자등록번호 (숫자만, 10자리)"
    )
    address = models.CharField(
        max_length=300, blank=True, help_text="사업장 주소"
    )
    business_type = models.CharField(
        max_length=100, blank=True, help_text="업태"
    )
    business_item = models.CharField(
        max_length=100, blank=True, help_text="종목"
    )
    tax_invoice_email = models.EmailField(help_text="세금계산서 수신 이메일")
    manager_name = models.CharField(
        max_length=100, blank=True, help_text="담당자 이름"
    )
    manager_phone = models.CharField(
        max_length=20, blank=True, help_text="담당자 전화번호"
    )
    manager_email = models.EmailField(blank=True, help_text="담당자 이메일")

    class Meta:
        db_table = "business_profile"
        verbose_name = "사업자 정보"
        verbose_name_plural = "사업자 정보"

    def __str__(self):
        return f"BusinessProfile({self.tenant.code}, {self.business_name})"

    def to_snapshot(self) -> dict:
        """세금계산서 발행 시점의 사업자 정보 스냅샷"""
        return {
            "business_name": self.business_name,
            "representative_name": self.representative_name,
            "business_registration_number": self.business_registration_number,
            "address": self.address,
            "business_type": self.business_type,
            "business_item": self.business_item,
            "tax_invoice_email": self.tax_invoice_email,
            "manager_name": self.manager_name,
            "manager_phone": self.manager_phone,
        }


class Invoice(TimestampModel):
    """청구서 — 구독 기간당 1건 생성"""

    tenant = models.ForeignKey(
        "core.Tenant", on_delete=models.CASCADE, related_name="invoices"
    )
    invoice_number = models.CharField(
        max_length=30, unique=True, help_text="청구서 번호 — 사람이 보는 표시용 (예: INV-2026-03-001)"
    )
    provider_order_id = models.CharField(
        max_length=64, unique=True, blank=True,
        help_text="PG 주문 식별용 고유 ID (UUID 기반, invoice_number와 분리)",
    )

    # 구독 정보 스냅샷
    plan = models.CharField(max_length=20, help_text="청구 시점 플랜")
    billing_mode = models.CharField(
        max_length=20, help_text="AUTO_CARD 또는 INVOICE_REQUEST"
    )

    # 금액
    supply_amount = models.PositiveIntegerField(
        help_text="공급가액 (부가세 제외)"
    )
    tax_amount = models.PositiveIntegerField(default=0, help_text="부가세")
    total_amount = models.PositiveIntegerField(
        help_text="합계 (공급가액 + 부가세)"
    )

    # 기간
    period_start = models.DateField(help_text="구독 기간 시작일")
    period_end = models.DateField(help_text="구독 기간 종료일")
    due_date = models.DateField(help_text="결제 기한")

    # 상태
    status = models.CharField(
        max_length=20, choices=INVOICE_STATUS_CHOICES, default="SCHEDULED"
    )
    paid_at = models.DateTimeField(
        null=True, blank=True, help_text="결제 완료 시각"
    )
    failed_at = models.DateTimeField(
        null=True, blank=True, help_text="마지막 결제 실패 시각"
    )
    failure_reason = models.TextField(
        blank=True, help_text="마지막 결제 실패 사유"
    )
    attempt_count = models.PositiveSmallIntegerField(
        default=0, help_text="결제 시도 횟수"
    )
    next_retry_at = models.DateField(
        null=True, blank=True, help_text="다음 재시도 예정일"
    )

    memo = models.TextField(blank=True)

    class Meta:
        db_table = "billing_invoice"
        verbose_name = "청구서"
        verbose_name_plural = "청구서"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["due_date"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "period_start", "period_end"],
                name="unique_invoice_per_period",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.provider_order_id:
            self.provider_order_id = f"ord_{uuid.uuid4().hex}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Invoice({self.invoice_number}, {self.status})"


class PaymentTransaction(TimestampModel):
    """결제 트랜잭션 — 청구서 1건에 여러 시도 가능"""

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="payment_transactions",
    )
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="transactions",
        null=True,
        blank=True,
    )

    # PG 정보
    transaction_key = models.CharField(
        max_length=200, blank=True, help_text="PG 트랜잭션 키 (레거시, provider_payment_key 사용 권장)"
    )
    provider_payment_key = models.CharField(
        max_length=200, blank=True, help_text="PG사 결제 키 (Toss: paymentKey)"
    )
    provider_order_id = models.CharField(
        max_length=64, blank=True, help_text="PG 주문 ID (Invoice.provider_order_id와 동일)"
    )
    idempotency_key = models.CharField(
        max_length=64, unique=True, blank=True, null=True,
        help_text="멱등성 키 — 동일 결제 중복 방지"
    )
    provider = models.CharField(max_length=30, blank=True, help_text="PG사")
    payment_method = models.CharField(
        max_length=30, blank=True, help_text="결제 수단 (card, transfer 등)"
    )

    # 금액
    amount = models.PositiveIntegerField(help_text="결제 금액")

    # 상태
    status = models.CharField(
        max_length=20, choices=PAYMENT_STATUS_CHOICES, default="PENDING"
    )

    # 카드 정보 (카드 결제인 경우)
    card_company = models.CharField(max_length=50, blank=True)
    card_number_masked = models.CharField(max_length=20, blank=True)

    # 요청/응답
    request_payload = models.JSONField(default=dict, help_text="PG 요청 페이로드")
    response_payload = models.JSONField(default=dict, help_text="PG 응답 페이로드 (원본)")
    raw_response = models.JSONField(default=dict, help_text="PG 원본 응답 (레거시, response_payload 사용 권장)")

    # 결과
    failure_reason = models.TextField(blank=True, help_text="실패 사유")
    processed_at = models.DateTimeField(
        null=True, blank=True, help_text="처리 완료 시각"
    )
    reconciled_at = models.DateTimeField(
        null=True, blank=True, help_text="대사 완료 시각"
    )

    # 환불
    refunded_amount = models.PositiveIntegerField(default=0)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "billing_payment_transaction"
        verbose_name = "결제 트랜잭션"
        verbose_name_plural = "결제 트랜잭션"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self):
        return f"PaymentTx({self.id}, {self.status}, \u20a9{self.amount})"


class TaxInvoiceIssue(TimestampModel):
    """세금계산서 발행 추적"""

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="tax_invoice_issues",
    )
    invoice = models.OneToOneField(
        Invoice, on_delete=models.CASCADE, related_name="tax_invoice_issue"
    )

    # 발행 시점 사업자 정보 (변경되어도 발행 당시 정보 유지)
    business_profile_snapshot = models.JSONField(
        default=dict, help_text="발행 시점 사업자 정보 스냅샷"
    )

    # 상태
    status = models.CharField(
        max_length=20, choices=TAX_INVOICE_STATUS_CHOICES, default="NOT_REQUESTED"
    )

    # 발행 정보
    issue_number = models.CharField(
        max_length=50, blank=True, help_text="국세청 승인번호"
    )
    issued_at = models.DateTimeField(
        null=True, blank=True, help_text="발행 완료 시각"
    )
    requested_at = models.DateTimeField(
        null=True, blank=True, help_text="발행 요청 시각"
    )

    failure_reason = models.TextField(blank=True, help_text="발행 실패 사유")
    memo = models.TextField(blank=True)

    class Meta:
        db_table = "billing_tax_invoice_issue"
        verbose_name = "세금계산서 발행"
        verbose_name_plural = "세금계산서 발행"
        ordering = ["-created_at"]

    def __str__(self):
        return f"TaxInvoice({self.invoice.invoice_number}, {self.status})"
