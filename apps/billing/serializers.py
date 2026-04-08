"""
Billing API Serializers.
"""

from rest_framework import serializers

from apps.billing.models import (
    BillingKey,
    BillingProfile,
    BusinessProfile,
    Invoice,
    PaymentTransaction,
)


# ──────────────────────────────────────────────
# Invoice
# ──────────────────────────────────────────────

class InvoiceListSerializer(serializers.ModelSerializer):
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
            "paid_at",
            "failed_at",
            "attempt_count",
            "created_at",
        ]


class InvoiceDetailSerializer(serializers.ModelSerializer):
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


# ──────────────────────────────────────────────
# Admin: 테넌트 구독 현황
# ──────────────────────────────────────────────

class TenantSubscriptionSummarySerializer(serializers.Serializer):
    """플랫폼 관리자용 테넌트 구독 현황 요약"""
    tenant_id = serializers.IntegerField()
    tenant_code = serializers.CharField()
    tenant_name = serializers.CharField()
    plan = serializers.CharField()
    plan_display = serializers.CharField()
    monthly_price = serializers.IntegerField()
    subscription_status = serializers.CharField()
    subscription_status_display = serializers.CharField()
    subscription_expires_at = serializers.DateField(allow_null=True)
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


class ChangePlanSerializer(serializers.Serializer):
    plan = serializers.ChoiceField(choices=["standard", "pro", "max"])


class MarkPaidSerializer(serializers.Serializer):
    memo = serializers.CharField(required=False, allow_blank=True, default="")
