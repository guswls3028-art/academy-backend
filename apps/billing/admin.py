from django.contrib import admin

from .models import (
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
    )
    list_filter = ("status", "billing_mode", "plan")
    search_fields = ("invoice_number",)
    raw_id_fields = ("tenant",)
    date_hierarchy = "due_date"


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
    )
    list_filter = ("status", "payment_method", "provider")
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
