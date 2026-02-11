# PATH: apps/core/serializers.py
from rest_framework import serializers
from django.contrib.auth import get_user_model

from apps.core.models import Attendance, Expense, TenantMembership, Program

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    tenantRole = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "name",
            "phone",
            "is_staff",
            "is_superuser",
            "tenantRole",
        ]

    def get_tenantRole(self, user):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None)

        if not tenant:
            return None

        membership = (
            TenantMembership.objects
            .filter(tenant=tenant, user=user, is_active=True)
            .only("role")
            .first()
        )

        return membership.role if membership else None


class ProgramPublicSerializer(serializers.ModelSerializer):
    tenantCode = serializers.SerializerMethodField()

    class Meta:
        model = Program
        fields = [
            "tenantCode",
            "display_name",
            "brand_key",
            "login_variant",
            "plan",
            "feature_flags",
            "ui_config",
            "is_active",
        ]

    def get_tenantCode(self, obj: Program) -> str:
        return obj.tenant.code


class ProgramUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Program
        fields = [
            "display_name",
            "brand_key",
            "login_variant",
            "plan",
            "feature_flags",
            "ui_config",
            "is_active",
        ]


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "phone"]


class AttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attendance
        fields = "__all__"
        read_only_fields = ["user", "tenant", "duration_hours", "amount"]


class ExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense
        fields = "__all__"
        read_only_fields = ["user", "tenant"]
