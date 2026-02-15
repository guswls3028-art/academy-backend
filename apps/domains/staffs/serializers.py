# PATH: apps/domains/staffs/serializers.py
from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import (
    Staff,
    WorkType,
    StaffWorkType,
    WorkRecord,
    ExpenseRecord,
    WorkMonthLock,
    PayrollSnapshot,
)
from academy.adapters.db.django import repositories_staffs as staff_repo
from academy.adapters.db.django import repositories_teachers as teacher_repo
from academy.adapters.db.django import repositories_students as students_repo
from academy.adapters.db.django import repositories_core as core_repo

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
        queryset=staff_repo.work_type_empty_queryset(),
        write_only=True,
    )
    effective_hourly_wage = serializers.IntegerField(read_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request") if self.context else None
        tenant = getattr(request, "tenant", None) if request else None
        self.fields["work_type_id"].queryset = (
            staff_repo.work_type_queryset_tenant(tenant) if tenant else staff_repo.work_type_all()
        )

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
    role = serializers.SerializerMethodField()

    class Meta:
        model = Staff
        fields = [
            "id",
            "name",
            "phone",
            "is_active",
            "is_manager",
            "pay_type",
            "role",
            "staff_work_types",
            "created_at",
            "updated_at",
        ]
        ref_name = "StaffList"

    def get_role(self, obj):
        if teacher_repo.teacher_exists_tenant_name_phone(obj.tenant, obj.name, obj.phone or ""):
            return "TEACHER"
        return "ASSISTANT"


class StaffDetailSerializer(serializers.ModelSerializer):
    staff_work_types = StaffWorkTypeSerializer(many=True, read_only=True)
    role = serializers.SerializerMethodField()

    user_username = serializers.CharField(
        source="user.username",
        read_only=True,
    )
    user_is_staff = serializers.BooleanField(
        source="user.is_staff",
        read_only=True,
    )

    class Meta:
        model = Staff
        fields = [
            "id",
            "user",
            "user_username",
            "user_is_staff",
            "name",
            "phone",
            "is_active",
            "is_manager",
            "pay_type",
            "role",
            "staff_work_types",
            "created_at",
            "updated_at",
        ]
        ref_name = "StaffDetail"

    def get_role(self, obj):
        if teacher_repo.teacher_exists_tenant_name_phone(obj.tenant, obj.name, obj.phone or ""):
            return "TEACHER"
        return "ASSISTANT"


# ======================================================
# 🔥 Staff CREATE / UPDATE / DELETE (ROLE 포함)
# ======================================================

class StaffCreateUpdateSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(
        choices=[("TEACHER", "강사"), ("ASSISTANT", "조교")],
        write_only=True,
        required=True,
    )
    username = serializers.CharField(write_only=True, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Staff
        fields = [
            "user",
            "username",
            "password",
            "name",
            "phone",
            "is_active",
            "is_manager",
            "pay_type",
            "role",
        ]
        ref_name = "StaffWrite"
        extra_kwargs = {"user": {"required": False}}

    # =========================
    # CREATE
    # =========================
    def create(self, validated_data):
        role = validated_data.pop("role")
        username = (validated_data.pop("username", None) or "").strip()
        password = validated_data.pop("password", None) or ""
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None

        user = None
        if username and password and tenant:
            user = students_repo.user_create_user(
                username=username,
                password=password,
                name=validated_data.get("name") or username,
                phone=validated_data.get("phone") or "",
            )
            core_repo.membership_ensure_active(
                tenant=tenant,
                user=user,
                role="teacher" if role == "TEACHER" else "staff",
            )
            validated_data["user"] = user

        validated_data["tenant"] = tenant
        staff = super().create(validated_data)

        if role == "TEACHER":
            self._create_teacher(staff)
            self._grant_user_staff_permission(staff)

        return staff

    # =========================
    # UPDATE (is_active sync, role 무시)
    # =========================
    def update(self, instance, validated_data):
        validated_data.pop("role", None)  # role은 create 전용
        is_active_before = instance.is_active
        staff = super().update(instance, validated_data)

        if is_active_before and staff.is_active is False:
            teacher_repo.teacher_update_is_active_by_name_phone(staff.name, staff.phone or "", False)

        return staff

    # =========================
    # DELETE (Staff + Teacher + User)
    # =========================
    def delete(self, instance):
        user = instance.user

        teacher_repo.teacher_delete_by_name_phone(instance.name, instance.phone or "")

        # 🔥 Staff 삭제
        instance.delete()

        # 🔥 User 삭제
        if user:
            user.delete()

    # =========================
    # Helpers
    # =========================
    def _create_teacher(self, staff: Staff):
        teacher_repo.teacher_create(
            staff.tenant,
            staff.name,
            staff.phone or "",
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
    locked_by_name = serializers.CharField(source="locked_by.username", read_only=True)

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
            "locked_by_name",
            "created_at",
        ]
        read_only_fields = ["locked_by", "created_at"]
        ref_name = "WorkMonthLock"


class PayrollSnapshotSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)
    generated_by_name = serializers.CharField(source="generated_by.username", read_only=True)

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
            "generated_by_name",
            "created_at",
        ]
        read_only_fields = fields
        ref_name = "PayrollSnapshot"
