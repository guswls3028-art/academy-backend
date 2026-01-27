# PATH: apps/domains/staffs/serializers.py
from rest_framework import serializers
from django.contrib.auth import get_user_model

from apps.domains.teachers.models import Teacher
from .models import (
    Staff,
    WorkType,
    StaffWorkType,
    WorkRecord,
    ExpenseRecord,
    WorkMonthLock,
    PayrollSnapshot,
)

User = get_user_model()

# ---------------------------
# WorkType
# ---------------------------

class WorkTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkType
        fields = [
            "id",
            "name",
            "base_hourly_wage",
            "color",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        ref_name = "StaffWorkTypeDefinition"


# ---------------------------
# StaffWorkType
# ---------------------------

class StaffWorkTypeSerializer(serializers.ModelSerializer):
    work_type = WorkTypeSerializer(read_only=True)
    work_type_id = serializers.PrimaryKeyRelatedField(
        source="work_type",
        queryset=WorkType.objects.all(),
        write_only=True,
    )
    effective_hourly_wage = serializers.IntegerField(read_only=True)

    class Meta:
        model = StaffWorkType
        fields = [
            "id",
            "staff",
            "work_type",
            "work_type_id",
            "hourly_wage",
            "effective_hourly_wage",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["staff", "created_at", "updated_at"]
        ref_name = "StaffWorkType"


# ---------------------------
# Staff (LIST / DETAIL)
# ---------------------------

class StaffListSerializer(serializers.ModelSerializer):
    staff_work_types = StaffWorkTypeSerializer(many=True, read_only=True)

    class Meta:
        model = Staff
        fields = [
            "id",
            "name",
            "phone",
            "is_active",
            "is_manager",
            "pay_type",
            "staff_work_types",
            "created_at",
            "updated_at",
        ]
        ref_name = "StaffList"


class StaffDetailSerializer(serializers.ModelSerializer):
    staff_work_types = StaffWorkTypeSerializer(many=True, read_only=True)

    class Meta:
        model = Staff
        fields = [
            "id",
            "user",
            "name",
            "phone",
            "is_active",
            "is_manager",
            "pay_type",
            "staff_work_types",
            "created_at",
            "updated_at",
        ]
        ref_name = "StaffDetail"


# ======================================================
# 🔥 Staff CREATE / UPDATE / DELETE (ROLE 포함)
# ======================================================

class StaffCreateUpdateSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(
        choices=[("TEACHER", "강사"), ("ASSISTANT", "조교")],
        write_only=True,
        required=True,
    )

    class Meta:
        model = Staff
        fields = [
            "user",
            "name",
            "phone",
            "is_active",
            "is_manager",
            "pay_type",
            "role",
        ]
        ref_name = "StaffWrite"

    # =========================
    # CREATE
    # =========================
    def create(self, validated_data):
        role = validated_data.pop("role")
        staff = super().create(validated_data)

        if role == "TEACHER":
            self._create_teacher(staff)
            self._grant_user_staff_permission(staff)

        return staff

    # =========================
    # UPDATE (is_active sync)
    # =========================
    def update(self, instance, validated_data):
        is_active_before = instance.is_active
        staff = super().update(instance, validated_data)

        if is_active_before and staff.is_active is False:
            Teacher.objects.filter(
                name=staff.name,
                phone=staff.phone,
            ).update(is_active=False)

        return staff

    # =========================
    # DELETE (Teacher 같이 제거)
    # =========================
    def delete(self, instance):
        Teacher.objects.filter(
            name=instance.name,
            phone=instance.phone,
        ).delete()
        instance.delete()

    # =========================
    # Helpers
    # =========================
    def _create_teacher(self, staff: Staff):
        Teacher.objects.create(
            name=staff.name,
            phone=staff.phone,
            is_active=True,
        )

    def _grant_user_staff_permission(self, staff: Staff):
        if not staff.user:
            return

        user: User = staff.user
        if not user.is_staff:
            user.is_staff = True
            user.save(update_fields=["is_staff"])


# ---------------------------
# WorkRecord
# ---------------------------

class WorkRecordSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)
    work_type_name = serializers.CharField(source="work_type.name", read_only=True)

    class Meta:
        model = WorkRecord
        fields = [
            "id",
            "staff",
            "staff_name",
            "work_type",
            "work_type_name",
            "date",
            "start_time",
            "end_time",
            "break_minutes",
            "work_hours",
            "amount",
            "memo",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["work_hours", "amount", "created_at", "updated_at"]
        ref_name = "StaffWorkRecord"


# ---------------------------
# ExpenseRecord
# ---------------------------

class ExpenseRecordSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)
    approved_by_name = serializers.CharField(
        source="approved_by.username",
        read_only=True,
    )

    class Meta:
        model = ExpenseRecord
        fields = [
            "id",
            "staff",
            "staff_name",
            "date",
            "title",
            "amount",
            "memo",
            "status",
            "approved_at",
            "approved_by",
            "approved_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "approved_at",
            "approved_by",
            "created_at",
            "updated_at",
        ]
        ref_name = "StaffExpenseRecord"


# ---------------------------
# WorkMonthLock / Payroll
# ---------------------------

class WorkMonthLockSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)

    class Meta:
        model = WorkMonthLock
        fields = [
            "id",
            "staff",
            "staff_name",
            "year",
            "month",
            "is_locked",
            "locked_by",
            "created_at",
        ]
        read_only_fields = ["locked_by", "created_at"]
        ref_name = "WorkMonthLock"


class PayrollSnapshotSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)

    class Meta:
        model = PayrollSnapshot
        fields = [
            "id",
            "staff",
            "staff_name",
            "year",
            "month",
            "work_hours",
            "work_amount",
            "approved_expense_amount",
            "total_amount",
            "generated_by",
            "created_at",
        ]
        read_only_fields = fields
        ref_name = "PayrollSnapshot"
