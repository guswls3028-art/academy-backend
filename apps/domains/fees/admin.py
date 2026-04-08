from django.contrib import admin

from .models import FeeTemplate, StudentFee, StudentInvoice, InvoiceItem, FeePayment


@admin.register(FeeTemplate)
class FeeTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "fee_type", "billing_cycle", "amount", "lecture", "is_active")
    list_filter = ("fee_type", "billing_cycle", "is_active")
    search_fields = ("name",)


@admin.register(StudentFee)
class StudentFeeAdmin(admin.ModelAdmin):
    list_display = ("student", "tenant", "fee_template", "effective_amount", "discount_amount", "is_active")
    list_filter = ("is_active",)
    search_fields = ("student__name",)


@admin.register(StudentInvoice)
class StudentInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "tenant", "student", "billing_year", "billing_month", "total_amount", "paid_amount", "status", "due_date")
    list_filter = ("status", "billing_year", "billing_month")
    search_fields = ("invoice_number", "student__name")
    date_hierarchy = "due_date"


@admin.register(InvoiceItem)
class InvoiceItemAdmin(admin.ModelAdmin):
    list_display = ("invoice", "description", "amount")


@admin.register(FeePayment)
class FeePaymentAdmin(admin.ModelAdmin):
    list_display = ("student", "tenant", "invoice", "amount", "payment_method", "status", "paid_at")
    list_filter = ("payment_method", "status")
    search_fields = ("student__name",)
