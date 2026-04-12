# PATH: apps/core/serializers.py
from rest_framework import serializers
from django.contrib.auth import get_user_model

from apps.core.models import Attendance, Expense, TenantMembership, Program
from academy.adapters.db.django import repositories_core as core_repo
from apps.infrastructure.storage import r2 as r2_storage

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    tenantRole = serializers.SerializerMethodField()
    linkedStudentId = serializers.SerializerMethodField()
    linkedStudentName = serializers.SerializerMethodField()
    linkedStudents = serializers.SerializerMethodField()

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
            "linkedStudentId",
            "linkedStudentName",
            "linkedStudents",
        ]

    def get_tenantRole(self, user):
        try:
            request = self.context.get("request")
            tenant = getattr(request, "tenant", None)
            if not tenant:
                return None
            membership = core_repo.membership_get(tenant=tenant, user=user, is_active=True)
            return membership.role if membership else None
        except Exception:
            return None

    def get_linkedStudentId(self, user):
        """학부모(role=parent)일 때 연결된 학생 ID (첫 번째)"""
        try:
            request = self.context.get("request")
            tenant = getattr(request, "tenant", None)
            if not tenant:
                return None
            membership = core_repo.membership_get(tenant=tenant, user=user, is_active=True)
            if not membership or membership.role != "parent":
                return None
            parent = core_repo.parent_get_by_user(user)
            if not parent:
                return None
            first_student = parent.students.filter(deleted_at__isnull=True).first()
            return first_student.id if first_student else None
        except Exception:
            return None

    def get_linkedStudentName(self, user):
        """학부모일 때 연결된 첫 학생 이름. 표시용 '{name} 학생 학부모님'"""
        try:
            request = self.context.get("request")
            tenant = getattr(request, "tenant", None)
            if not tenant:
                return None
            membership = core_repo.membership_get(tenant=tenant, user=user, is_active=True)
            if not membership or membership.role != "parent":
                return None
            parent = core_repo.parent_get_by_user(user)
            if not parent:
                return None
            first_student = parent.students.filter(deleted_at__isnull=True).first()
            return (first_student.name or "").strip() if first_student else None
        except Exception:
            return None

    def get_linkedStudents(self, user):
        """학부모일 때 연결된 자녀 목록 (삭제되지 않은 학생만). [{ id, name }, ...]"""
        try:
            request = self.context.get("request")
            tenant = getattr(request, "tenant", None)
            if not tenant:
                return None
            membership = core_repo.membership_get(tenant=tenant, user=user, is_active=True)
            if not membership or membership.role != "parent":
                return None
            parent = core_repo.parent_get_by_user(user)
            if not parent:
                return None
            students = list(
                parent.students.filter(deleted_at__isnull=True).values_list("id", "name")
            )
            return [{"id": sid, "name": (name or "").strip() or "학생"} for sid, name in students]
        except Exception:
            return None


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
            "monthly_price",
            "subscription_status",
            "subscription_started_at",
            "subscription_expires_at",
            "feature_flags",
            "ui_config",
            "is_active",
        ]

    def get_tenantCode(self, obj: Program) -> str:
        tenant = getattr(obj, "tenant", None)
        return getattr(tenant, "code", "") or ""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        cfg = dict(instance.ui_config or {})
        resolved = r2_storage.resolve_admin_logo_url(
            logo_key=cfg.get("logo_key"),
            logo_url=cfg.get("logo_url"),
        )
        if resolved is not None:
            cfg["logo_url"] = resolved
        data["ui_config"] = cfg
        return data


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

    def _is_owner_or_superuser(self):
        request = self.context.get("request")
        if not request:
            return False
        from apps.core.models import TenantMembership
        tenant = getattr(request, "tenant", None)
        user = getattr(request, "user", None)
        if not tenant or not user:
            return False
        if getattr(user, "is_superuser", False):
            return True
        return TenantMembership.objects.filter(
            user=user, tenant=tenant, is_active=True, role="owner"
        ).exists()

    def validate_plan(self, value):
        """플랜 변경은 owner만 가능."""
        if not self._is_owner_or_superuser():
            raise serializers.ValidationError("플랜 변경은 대표만 가능합니다.")
        return value

    def validate_is_active(self, value):
        """프로그램 활성화/비활성화는 owner만 가능."""
        if not self._is_owner_or_superuser():
            raise serializers.ValidationError("프로그램 활성화/비활성화는 대표만 가능합니다.")
        return value

    def validate_feature_flags(self, value):
        """기능 플래그 변경은 owner만 가능. clinic_mode="regular"는 section_mode=true 필수."""
        if not self._is_owner_or_superuser():
            raise serializers.ValidationError("기능 설정 변경은 대표만 가능합니다.")
        if isinstance(value, dict):
            if value.get("clinic_mode") == "regular" and not value.get("section_mode"):
                raise serializers.ValidationError(
                    "정규형 클리닉 모드(clinic_mode=regular)는 반 편성 모드(section_mode)가 "
                    "활성화되어야 사용할 수 있습니다."
                )
        return value


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
