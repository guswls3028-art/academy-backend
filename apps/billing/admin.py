from django.contrib import admin

from .models import (
    BankTransferNotice,
    BillingProfile,
    BillingKey,
    BusinessProfile,
    Invoice,
    PaymentTransaction,
    TaxInvoiceIssue,
)


@admin.register(BillingProfile)
class BillingProfileAdmin(admin.ModelAdmin):
    list_display = ("tenant", "provider", "payer_name", "payer_email")
    list_filter = ("provider",)
    raw_id_fields = ("tenant",)


@admin.register(BillingKey)
class BillingKeyAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "card_company",
        "card_number_masked",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "provider", "card_company")
    raw_id_fields = ("tenant", "billing_profile")
    exclude = ("billing_key",)


@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "business_name",
        "representative_name",
        "business_registration_number",
        "tax_invoice_email",
    )
    search_fields = ("business_name", "business_registration_number")
    raw_id_fields = ("tenant",)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "tenant",
        "plan",
        "total_amount",
        "status",
        "billing_mode",
        "due_date",
        "paid_at",
        "attempt_count",
    )
    list_filter = ("status", "billing_mode", "plan")
    search_fields = ("invoice_number", "provider_order_id")
    raw_id_fields = ("tenant",)
    date_hierarchy = "due_date"
    readonly_fields = ("provider_order_id",)


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "amount",
        "status",
        "payment_method",
        "card_company",
        "processed_at",
        "reconciled_at",
    )
    list_filter = ("status", "payment_method", "provider")
    search_fields = ("provider_payment_key", "provider_order_id", "idempotency_key")
    raw_id_fields = ("tenant", "invoice")


@admin.register(TaxInvoiceIssue)
class TaxInvoiceIssueAdmin(admin.ModelAdmin):
    list_display = (
        "invoice",
        "tenant",
        "status",
        "issue_number",
        "issued_at",
        "requested_at",
    )
    list_filter = ("status",)
    raw_id_fields = ("tenant", "invoice")


@admin.register(BankTransferNotice)
class BankTransferNoticeAdmin(admin.ModelAdmin):
    list_display = (
        "invoice",
        "depositor_name",
        "amount",
        "status",
        "tax_invoice_requested",
        "deposited_at",
        "submitted_at",
    )
    list_filter = ("status", "tax_invoice_requested")
    search_fields = (
        "invoice__invoice_number",
        "invoice__tenant__code",
        "depositor_name",
    )
    readonly_fields = (
        "invoice",
        "depositor_name",
        "deposited_at",
        "amount",
        "status",
        "tax_invoice_requested",
        "business_profile_snapshot",
        "submitted_at",
        "reviewed_at",
        "reviewed_by",
        "rejection_reason",
        "memo",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
