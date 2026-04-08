# PATH: apps/domains/fees/serializers.py

from rest_framework import serializers

from .models import (
    FeeTemplate,
    StudentFee,
    StudentInvoice,
    InvoiceItem,
    FeePayment,
)


# ========================================================
# FeeTemplate
# ========================================================

class FeeTemplateSerializer(serializers.ModelSerializer):
    fee_type_display = serializers.CharField(source="get_fee_type_display", read_only=True)
    billing_cycle_display = serializers.CharField(source="get_billing_cycle_display", read_only=True)
    lecture_title = serializers.CharField(source="lecture.title", read_only=True, default=None)

    class Meta:
        model = FeeTemplate
        fields = [
            "id", "name", "fee_type", "fee_type_display",
            "billing_cycle", "billing_cycle_display",
            "amount", "lecture", "lecture_title",
            "auto_assign", "is_active", "memo",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class FeeTemplateCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeTemplate
        fields = [
            "name", "fee_type", "billing_cycle", "amount",
            "lecture", "auto_assign", "is_active", "memo",
        ]


# ========================================================
# StudentFee
# ========================================================

class StudentFeeSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.name", read_only=True)
    fee_template_name = serializers.CharField(source="fee_template.name", read_only=True)
    fee_type = serializers.CharField(source="fee_template.fee_type", read_only=True)
    effective_amount = serializers.IntegerField(read_only=True)
    lecture_title = serializers.CharField(source="fee_template.lecture.title", read_only=True, default=None)

    class Meta:
        model = StudentFee
        fields = [
            "id", "student", "student_name",
            "fee_template", "fee_template_name", "fee_type",
            "enrollment", "lecture_title",
            "adjusted_amount", "discount_amount", "discount_reason",
            "billing_start_month", "billing_end_month",
            "effective_amount", "is_active",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class StudentFeeBulkAssignSerializer(serializers.Serializer):
    student_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        max_length=200,
    )
    fee_template_id = serializers.IntegerField()


# ========================================================
# InvoiceItem
# ========================================================

class InvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItem
        fields = ["id", "description", "amount", "fee_template"]
        read_only_fields = ["id"]


# ========================================================
# StudentInvoice
# ========================================================

class StudentInvoiceListSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    outstanding_amount = serializers.IntegerField(read_only=True)

    class Meta:
        model = StudentInvoice
        fields = [
            "id", "invoice_number",
            "student", "student_name",
            "billing_year", "billing_month",
            "total_amount", "paid_amount", "outstanding_amount",
            "status", "status_display",
            "due_date", "paid_at",
            "memo", "created_at",
        ]


class StudentInvoiceDetailSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.name", read_only=True)
    student_phone = serializers.CharField(source="student.phone", read_only=True, default=None)
    student_parent_phone = serializers.CharField(source="student.parent_phone", read_only=True, default=None)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    outstanding_amount = serializers.IntegerField(read_only=True)
    items = InvoiceItemSerializer(many=True, read_only=True)
    payments = serializers.SerializerMethodField()

    class Meta:
        model = StudentInvoice
        fields = [
            "id", "invoice_number",
            "student", "student_name", "student_phone", "student_parent_phone",
            "billing_year", "billing_month",
            "total_amount", "paid_amount", "outstanding_amount",
            "status", "status_display",
            "due_date", "paid_at",
            "memo", "created_by",
            "items", "payments",
            "created_at", "updated_at",
        ]

    def get_payments(self, obj):
        payments = obj.payments.filter(status="SUCCESS").order_by("-paid_at")
        return FeePaymentSerializer(payments, many=True).data


class GenerateInvoicesSerializer(serializers.Serializer):
    billing_year = serializers.IntegerField(min_value=2020, max_value=2100)
    billing_month = serializers.IntegerField(min_value=1, max_value=12)
    due_date = serializers.DateField()


# ========================================================
# FeePayment
# ========================================================

class FeePaymentSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.name", read_only=True)
    payment_method_display = serializers.CharField(source="get_payment_method_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)

    class Meta:
        model = FeePayment
        fields = [
            "id", "invoice", "invoice_number",
            "student", "student_name",
            "amount", "payment_method", "payment_method_display",
            "status", "status_display",
            "paid_at", "recorded_by",
            "receipt_note", "memo",
            "created_at",
        ]
        read_only_fields = ["id", "student", "recorded_by", "created_at"]


class RecordPaymentSerializer(serializers.Serializer):
    invoice_id = serializers.IntegerField()
    amount = serializers.IntegerField(min_value=1)
    payment_method = serializers.ChoiceField(choices=["CARD", "BANK_TRANSFER", "CASH", "OTHER"])
    paid_at = serializers.DateTimeField(required=False)
    receipt_note = serializers.CharField(required=False, allow_blank=True, default="")
    memo = serializers.CharField(required=False, allow_blank=True, default="")
    idempotency_key = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)
