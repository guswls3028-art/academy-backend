"""
Billing API Serializers.
"""

import re

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from apps.billing.models import (
    BankTransferNotice,
    BillingKey,
    BillingProfile,
    BusinessProfile,
    Invoice,
    PaymentTransaction,
)


# ──────────────────────────────────────────────
# Invoice
# ──────────────────────────────────────────────

class InvoiceStateContractSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    is_terminal = serializers.SerializerMethodField()
    can_mark_paid = serializers.SerializerMethodField()
    payment_blocked_reason = serializers.SerializerMethodField()
    has_bank_transfer_notice = serializers.SerializerMethodField()

    def get_has_bank_transfer_notice(self, obj: Invoice) -> bool:
        try:
            obj.bank_transfer_notice
        except ObjectDoesNotExist:
            return False
        return True

    def get_is_terminal(self, obj: Invoice) -> bool:
        return obj.status in {"PAID", "VOID"}

    def get_can_mark_paid(self, obj: Invoice) -> bool:
        return (
            obj.status in {"PENDING", "FAILED", "OVERDUE"}
            and not self.get_has_bank_transfer_notice(obj)
        )

    def get_payment_blocked_reason(self, obj: Invoice) -> str:
        if self.get_can_mark_paid(obj):
            return ""
        if (
            obj.status in {"PENDING", "FAILED", "OVERDUE"}
            and self.get_has_bank_transfer_notice(obj)
        ):
            return "bank_transfer_review_required"
        return {
            "SCHEDULED": "invoice_not_pending",
            "PAID": "already_paid",
            "VOID": "invoice_void",
        }.get(obj.status, "invoice_state_not_payable")


class InvoiceListSerializer(InvoiceStateContractSerializer):
    tenant_code = serializers.CharField(source="tenant.code", read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "invoice_number",
            "tenant_code",
            "plan",
            "billing_mode",
            "total_amount",
            "supply_amount",
            "tax_amount",
            "period_start",
            "period_end",
            "due_date",
            "status",
            "status_display",
            "is_terminal",
            "can_mark_paid",
            "payment_blocked_reason",
            "has_bank_transfer_notice",
            "paid_at",
            "failed_at",
            "attempt_count",
            "created_at",
        ]


class InvoiceDetailSerializer(InvoiceStateContractSerializer):
    tenant_code = serializers.CharField(source="tenant.code", read_only=True)
    tenant_name = serializers.CharField(source="tenant.name", read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "invoice_number",
            "provider_order_id",
            "tenant_code",
            "tenant_name",
            "plan",
            "billing_mode",
            "supply_amount",
            "tax_amount",
            "total_amount",
            "period_start",
            "period_end",
            "due_date",
            "status",
            "status_display",
            "is_terminal",
            "can_mark_paid",
            "payment_blocked_reason",
            "has_bank_transfer_notice",
            "paid_at",
            "failed_at",
            "failure_reason",
            "attempt_count",
            "next_retry_at",
            "memo",
            "created_at",
            "updated_at",
        ]


# ──────────────────────────────────────────────
# PaymentTransaction
# ──────────────────────────────────────────────

class PaymentTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentTransaction
        fields = [
            "id",
            "invoice",
            "provider",
            "provider_payment_key",
            "payment_method",
            "amount",
            "status",
            "card_company",
            "card_number_masked",
            "failure_reason",
            "processed_at",
            "created_at",
        ]


# ──────────────────────────────────────────────
# BillingProfile / BillingKey
# ──────────────────────────────────────────────

class BillingProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingProfile
        fields = [
            "id",
            "provider",
            "payer_name",
            "payer_email",
            "payer_phone",
        ]
        read_only_fields = ["id", "provider"]


class BillingKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingKey
        fields = [
            "id",
            "card_company",
            "card_number_masked",
            "is_active",
            "created_at",
        ]


class BusinessProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessProfile
        fields = [
            "id",
            "business_name",
            "representative_name",
            "business_registration_number",
            "address",
            "business_type",
            "business_item",
            "tax_invoice_email",
            "manager_name",
            "manager_phone",
            "manager_email",
        ]
        read_only_fields = ["id"]

    def validate_business_registration_number(self, value: str) -> str:
        number = re.sub(r"\D", "", value or "")
        if len(number) != 10:
            raise serializers.ValidationError(
                "사업자등록번호 10자리를 입력해 주세요."
            )
        digits = [int(char) for char in number]
        weights = [1, 3, 7, 1, 3, 7, 1, 3, 5]
        checksum = sum(
            digit * weight
            for digit, weight in zip(digits[:9], weights, strict=True)
        )
        checksum += (digits[8] * 5) // 10
        if (10 - checksum % 10) % 10 != digits[9]:
            raise serializers.ValidationError(
                "유효한 사업자등록번호를 입력해 주세요."
            )
        return number


# ──────────────────────────────────────────────
# Bank transfer / tax invoice
# ──────────────────────────────────────────────

class BankTransferNoticeSubmitSerializer(serializers.Serializer):
    invoice_id = serializers.IntegerField(min_value=1)
    depositor_name = serializers.CharField(max_length=100)
    deposited_at = serializers.DateTimeField()
    tax_invoice_requested = serializers.BooleanField(default=False)
    receipt_kind = serializers.ChoiceField(choices=["NONE", "BUSINESS_TAX", "PERSONAL_TAX", "CASH"], required=False)
    recipient_identifier = serializers.CharField(required=False, write_only=True, max_length=16)
    recipient_name = serializers.CharField(required=False, write_only=True, max_length=100)
    recipient_email = serializers.EmailField(required=False, write_only=True)

    def validate(self, data):
        kind = data.get("receipt_kind")
        if kind in ("CASH", "PERSONAL_TAX"):
            identifier = data.get("recipient_identifier", "").replace("-", "")
            pattern = r"01[016789][0-9]{7,8}" if kind == "CASH" else r"[0-9]{6}[1-8][0-9]{6}"
            if not re.fullmatch(pattern, identifier):
                raise serializers.ValidationError({"detail": "증빙 발급용 번호를 확인해 주세요."})
            data["recipient_identifier"] = identifier
            if kind == "PERSONAL_TAX" and not (data.get("recipient_name") and data.get("recipient_email")):
                raise serializers.ValidationError({"detail": "개인 세금계산서 수신자 이름과 이메일을 입력해 주세요."})
        elif any(field in data for field in ("recipient_identifier", "recipient_name", "recipient_email")):
            raise serializers.ValidationError({"detail": "선택한 증빙에는 개인 식별정보가 필요하지 않습니다."})
        if kind is not None:
            data["tax_invoice_requested"] = kind != "NONE"
        return data

    def validate_depositor_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("입금자명을 입력해 주세요.")
        return value


class BankTransferNoticeSerializer(serializers.ModelSerializer):
    tenant_code = serializers.CharField(
        source="invoice.tenant.code",
        read_only=True,
    )
    tenant_name = serializers.CharField(
        source="invoice.tenant.name",
        read_only=True,
    )
    invoice_number = serializers.CharField(
        source="invoice.invoice_number",
        read_only=True,
    )
    invoice_status = serializers.CharField(
        source="invoice.status",
        read_only=True,
    )
    supply_amount = serializers.IntegerField(
        source="invoice.supply_amount",
        read_only=True,
    )
    tax_amount = serializers.IntegerField(
        source="invoice.tax_amount",
        read_only=True,
    )
    period_start = serializers.DateField(
        source="invoice.period_start",
        read_only=True,
    )
    period_end = serializers.DateField(
        source="invoice.period_end",
        read_only=True,
    )
    due_date = serializers.DateField(
        source="invoice.due_date",
        read_only=True,
    )
    receipt_kind = serializers.SerializerMethodField()
    automatic_receipt = serializers.SerializerMethodField()
    receipt_provider_state = serializers.SerializerMethodField()
    receipt_error = serializers.SerializerMethodField()
    tax_invoice_issue_id = serializers.SerializerMethodField()
    tax_invoice_status = serializers.SerializerMethodField()
    tax_invoice_issue_number = serializers.SerializerMethodField()
    reviewed_by_name = serializers.CharField(
        source="reviewed_by.name",
        read_only=True,
        default="",
    )

    class Meta:
        model = BankTransferNotice
        fields = [
            "id",
            "tenant_code",
            "tenant_name",
            "invoice",
            "invoice_number",
            "invoice_status",
            "supply_amount",
            "tax_amount",
            "amount",
            "period_start",
            "period_end",
            "due_date",
            "depositor_name",
            "deposited_at",
            "status",
            "tax_invoice_requested",
            "receipt_kind", "automatic_receipt", "receipt_provider_state", "receipt_error",
            "tax_invoice_issue_id",
            "tax_invoice_status",
            "tax_invoice_issue_number",
            "business_profile_snapshot",
            "submitted_at",
            "reviewed_at",
            "reviewed_by_name",
            "rejection_reason",
            "memo",
        ]
        read_only_fields = fields

    def _tax_issue(self, obj: BankTransferNotice):
        try:
            return obj.invoice.tax_invoice_issue
        except ObjectDoesNotExist:
            return None

    def _receipt(self, obj):
        return getattr(self._tax_issue(obj), "popbill", None)

    def get_receipt_kind(self, obj) -> str:
        receipt = self._receipt(obj)
        return receipt.kind if receipt else ("BUSINESS_TAX" if obj.tax_invoice_requested else "NONE")

    def get_automatic_receipt(self, obj) -> bool:
        return self._receipt(obj) is not None

    def get_receipt_provider_state(self, obj) -> int:
        return getattr(self._receipt(obj), "provider_state", 0)

    def get_receipt_error(self, obj) -> str:
        issue = self._tax_issue(obj)
        return issue.failure_reason if issue and self._receipt(obj) else ""

    def get_tax_invoice_issue_id(self, obj: BankTransferNotice):
        issue = self._tax_issue(obj)
        return issue.pk if issue else None

    def get_tax_invoice_status(self, obj: BankTransferNotice):
        issue = self._tax_issue(obj)
        return issue.status if issue else "NOT_REQUESTED"

    def get_tax_invoice_issue_number(self, obj: BankTransferNotice):
        issue = self._tax_issue(obj)
        receipt = self._receipt(obj)
        if receipt and receipt.kind == "CASH":
            return receipt.cash_approval_number
        return issue.issue_number if issue else ""


class BankTransferRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)

    def validate_reason(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("반려 사유를 입력해 주세요.")
        return value


class TaxInvoiceMarkIssuedSerializer(serializers.Serializer):
    issue_number = serializers.CharField(max_length=50)
    issued_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_issue_number(self, value: str) -> str:
        value = re.sub(r"\D", "", value or "")
        if len(value) != 24:
            raise serializers.ValidationError(
                "국세청 승인번호 24자리를 입력해 주세요."
            )
        return value


# ──────────────────────────────────────────────
# Admin: 테넌트 구독 현황
# ──────────────────────────────────────────────

class TenantSubscriptionSummarySerializer(serializers.Serializer):
    """플랫폼 관리자용 테넌트 구독 현황 요약"""
    program_id = serializers.IntegerField()
    tenant_id = serializers.IntegerField()
    tenant_code = serializers.CharField()
    tenant_name = serializers.CharField()
    plan = serializers.CharField()
    plan_display = serializers.CharField()
    monthly_price = serializers.IntegerField()
    monthly_supply_amount = serializers.IntegerField()
    monthly_tax_amount = serializers.IntegerField()
    monthly_total_amount = serializers.IntegerField()
    monthly_price_includes_tax = serializers.BooleanField()
    vat_rate_percent = serializers.IntegerField(allow_null=True)
    billing_price_policy = serializers.CharField()
    is_contract_price = serializers.BooleanField()
    has_lifetime_price_guarantee = serializers.BooleanField()
    price_guarantee_code = serializers.CharField(allow_null=True)
    price_guarantee_label = serializers.CharField(allow_null=True)
    billing_price_integrity = serializers.CharField()
    is_billing_price_ready = serializers.BooleanField()
    subscription_status = serializers.CharField()
    subscription_status_display = serializers.CharField()
    subscription_expires_at = serializers.DateField(allow_null=True)
    service_access_expires_at = serializers.DateField(allow_null=True)
    grace_period_days = serializers.IntegerField()
    grace_expires_at = serializers.DateField(allow_null=True)
    days_remaining = serializers.IntegerField(allow_null=True)
    billing_mode = serializers.CharField()
    cancel_at_period_end = serializers.BooleanField()
    next_billing_at = serializers.DateField(allow_null=True)
    is_subscription_active = serializers.BooleanField()


# ──────────────────────────────────────────────
# Admin Actions
# ──────────────────────────────────────────────

class ExtendSubscriptionSerializer(serializers.Serializer):
    days = serializers.IntegerField(min_value=1, max_value=3650)


class MarkPaidSerializer(serializers.Serializer):
    memo = serializers.CharField(required=False, allow_blank=True, default="")
