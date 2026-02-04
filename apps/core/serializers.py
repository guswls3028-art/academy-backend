# ======================================================================
# PATH: apps/core/serializers.py
# ======================================================================
from rest_framework import serializers
from django.contrib.auth import get_user_model

from apps.core.models import Attendance, Expense, TenantMembership

User = get_user_model()


# ------------------------------------
# User Base (SSOT 강화)
# ------------------------------------

class UserSerializer(serializers.ModelSerializer):
    """
    ✅ Core User Serializer (Enterprise SSOT)

    - request.tenant 기준 TenantMembership.role 을
      tenantRole 필드로 반환
    - 프론트는 절대 role 추론 금지
    - 멀티테넌트 / 부모 / 학생 / 교직원 전부 대응
    """

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

        # tenant 미확정 (bypass path 등)
        if not tenant:
            return None

        membership = (
            TenantMembership.objects
            .filter(
                tenant=tenant,
                user=user,
                is_active=True,
            )
            .only("role")
            .first()
        )

        return membership.role if membership else None


# ------------------------------------
# Profile
# ------------------------------------

class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "phone"]


# ------------------------------------
# Attendance
# ------------------------------------

class AttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attendance
        fields = "__all__"
        read_only_fields = [
            "user",
            "tenant",
            "duration_hours",
            "amount",
        ]


# ------------------------------------
# Expense
# ------------------------------------

class ExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense
        fields = "__all__"
        read_only_fields = [
            "user",
            "tenant",
        ]
