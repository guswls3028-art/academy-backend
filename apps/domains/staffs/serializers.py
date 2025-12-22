# -*- coding: utf-8 -*-
# apps/staffs/serializers.py

from rest_framework import serializers

from .models import (
    Staff,
    WorkType,
    StaffWorkType,
    WorkRecord,
    ExpenseRecord,
)


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
# StaffWorkType (조교-근무유형)
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
# Staff
# ---------------------------

class StaffListSerializer(serializers.ModelSerializer):
    """
    Staff list serializer.
    Includes simplified work type info.
    """

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


class StaffCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Staff
        fields = [
            "user",
            "name",
            "phone",
            "is_active",
            "is_manager",
            "pay_type",
        ]
        ref_name = "StaffWrite"


# ---------------------------
# WorkRecord (출퇴근 기록)
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
# ExpenseRecord (비용 기록)
# ---------------------------

class ExpenseRecordSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.name", read_only=True)

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
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]
        ref_name = "StaffExpenseRecord"
