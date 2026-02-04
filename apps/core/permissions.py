# ======================================================================
# PATH: apps/core/permissions.py
# ======================================================================
from rest_framework.permissions import BasePermission

from apps.core.models import TenantMembership


class IsAdminOrStaff(BasePermission):
    """
    Django admin / staff 계정 전용
    (테넌트 무관, 내부 관리용)
    """

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or user.is_staff)
        )


class IsStudent(BasePermission):
    """
    학생 전용 Permission
    - 로그인 필수
    - User ↔ Student OneToOne 연결 필수
    """

    message = "Student account required."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and hasattr(user, "student_profile")
        )


class TenantResolvedAndMember(BasePermission):
    """
    ✅ Core 기본 Permission (SSOT)

    - request.tenant 가 resolve 되어야 함
    - 인증된 사용자
    - 활성 TenantMembership 존재

    ❗ role 은 여기서 절대 해석하지 않음
    """

    message = "Tenant membership required."

    def has_permission(self, request, view):
        tenant = getattr(request, "tenant", None)
        user = request.user

        if not tenant:
            return False

        if not user or not user.is_authenticated:
            return False

        return TenantMembership.objects.filter(
            tenant=tenant,
            user=user,
            is_active=True,
        ).exists()


class TenantResolvedAndStaff(BasePermission):
    """
    ✅ 운영레벨 Staff 전용 Permission

    허용 role:
    - owner
    - admin
    - staff
    - teacher

    학생 / 학부모 접근 차단용
    """

    message = "Staff membership required."

    STAFF_ROLES = ("owner", "admin", "staff", "teacher")

    def has_permission(self, request, view):
        tenant = getattr(request, "tenant", None)
        user = request.user

        if not tenant:
            return False

        if not user or not user.is_authenticated:
            return False

        return TenantMembership.objects.filter(
            tenant=tenant,
            user=user,
            is_active=True,
            role__in=self.STAFF_ROLES,
        ).exists()
