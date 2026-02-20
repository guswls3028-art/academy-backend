# PATH: apps/core/permissions.py
from rest_framework.permissions import BasePermission

from apps.core.models import TenantMembership


def is_effective_staff(user, tenant=None):
    """
    테넌트 내 슈퍼유저급 권한: Django is_superuser/is_staff 또는 해당 테넌트 스태프(owner/admin/staff/teacher).
    오너는 is_staff 없어도 프로그램 내 풀 권한. 운영상 테넌트는 항상 있음(미들웨어가 설정).
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    if not tenant:
        return False
    from academy.adapters.db.django import repositories_core as core_repo
    return core_repo.membership_exists_staff(
        tenant=tenant, user=user, staff_roles=("owner", "admin", "staff", "teacher")
    )


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


class IsSuperuserOnly(BasePermission):
    """
    슈퍼유저 전용 (개발자 전용).
    dev_app 등 본인만 쓰는 관리 기능용.
    """

    message = "Superuser only."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_superuser)


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


class TenantResolved(BasePermission):
    """
    ✅ Tenant Resolve only (SSOT)

    - request.tenant 가 resolve 되어야 함
    - 인증/멤버십은 요구하지 않음

    사용처:
    - 로그인 전 Public bootstrap (Program config, Tenant-bound public metadata)
    """

    message = "Tenant must be resolved."

    def has_permission(self, request, view):
        tenant = getattr(request, "tenant", None)
        return bool(tenant)


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

        from academy.adapters.db.django import repositories_core as core_repo
        return core_repo.membership_exists(tenant=tenant, user=user, is_active=True)


class TenantResolvedAndStaff(BasePermission):
    """
    ✅ 운영레벨 Staff 전용 Permission (오너 = 테넌트 내 슈퍼유저급)

    허용:
    - Django is_superuser / is_staff (테넌트 멤버십 없이도 통과, 충돌 방지)
    - request.tenant 기준 owner / admin / staff / teacher

    학생 / 학부모 접근 차단용
    """

    message = "Staff membership required."

    STAFF_ROLES = ("owner", "admin", "staff", "teacher")

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return False
        from academy.adapters.db.django import repositories_core as core_repo
        return core_repo.membership_exists_staff(tenant=tenant, user=user, staff_roles=self.STAFF_ROLES)


class TenantResolvedAndOwner(BasePermission):
    """
    ✅ Owner 전용 Permission

    dev_app 등 owner만 접근 가능한 기능용.
    """

    message = "Owner membership required."

    def has_permission(self, request, view):
        tenant = getattr(request, "tenant", None)
        user = request.user

        if not tenant:
            return False

        if not user or not user.is_authenticated:
            return False

        from academy.adapters.db.django import repositories_core as core_repo
        return core_repo.membership_exists_staff(tenant=tenant, user=user, staff_roles=("owner",))
