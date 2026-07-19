"""
Core Repository — Tenant, Program, User, TenantMembership, Attendance, Expense, TenantDomain.
ORM 접근은 메서드 내부에서만 lazy import.
"""
from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------


def program_get_by_tenant(tenant) -> Optional[Any]:
    from apps.core.models import Program
    return Program.objects.select_related("tenant").filter(tenant=tenant).first()


def program_get_by_tenant_only_feature_flags(tenant) -> Optional[Any]:
    from apps.core.models import Program
    return Program.objects.filter(tenant=tenant).only("feature_flags").first()


# ---------------------------------------------------------------------------
# Attendance / Expense
# ---------------------------------------------------------------------------


def attendance_filter(user, tenant, month: Optional[str] = None):
    from apps.core.models import Attendance
    qs = Attendance.objects.filter(user=user, tenant=tenant)
    if month:
        qs = qs.filter(date__startswith=month)
    return qs


def expense_filter(user, tenant, month: Optional[str] = None):
    from apps.core.models import Expense
    qs = Expense.objects.filter(user=user, tenant=tenant)
    if month:
        qs = qs.filter(date__startswith=month)
    return qs


# ---------------------------------------------------------------------------
# Tenant / TenantDomain
# ---------------------------------------------------------------------------


def tenant_get_by_id(tenant_id) -> Optional[Any]:
    from apps.core.models import Tenant
    return Tenant.objects.filter(id=tenant_id, is_active=True).first()


def tenant_get_by_id_any(tenant_id) -> Optional[Any]:
    from apps.core.models import Tenant
    return Tenant.objects.filter(id=tenant_id).first()


def tenant_get_by_code(code: str) -> Optional[Any]:
    """테넌트 코드로 조회 (활성만, 대소문자 무시). X-Tenant-Code 헤더 해석용."""
    from apps.core.models import Tenant
    raw = (code and str(code).strip()) or ""
    if not raw:
        return None
    return Tenant.objects.select_related("program").filter(code__iexact=raw, is_active=True).first()


def tenant_get_or_create(code: str, defaults: dict) -> tuple[Any, bool]:
    from apps.core.models import Tenant
    return Tenant.objects.get_or_create(code=code, defaults=defaults)


def tenant_first_active() -> Optional[Any]:
    from apps.core.models import Tenant
    return Tenant.objects.filter(is_active=True).order_by("id").first()


def tenant_domain_filter_by_host(host):
    from apps.core.models import TenantDomain
    return TenantDomain.objects.select_related("tenant__program").filter(host=host)


def tenant_domain_get_or_create(host: str, tenant, defaults: Optional[dict] = None):
    from apps.core.models import TenantDomain
    return TenantDomain.objects.get_or_create(
        host=host,
        tenant=tenant,
        defaults=defaults or {},
    )


def tenant_domain_get_or_create_by_defaults(host: str, defaults: dict) -> tuple[Any, bool]:
    from apps.core.models import TenantDomain
    return TenantDomain.objects.get_or_create(host=host, defaults=defaults)


def program_get_or_create(tenant, defaults: dict) -> tuple[Any, bool]:
    from apps.core.models import Program
    return Program.objects.get_or_create(tenant=tenant, defaults=defaults)


def tenant_domain_filter_by_tenant(tenant):
    from apps.core.models import TenantDomain
    return TenantDomain.objects.filter(tenant=tenant).order_by("host")


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


def user_get_by_username(username: str) -> Optional[Any]:
    """전역 조회 (tenant=null 사용자). 레거시/관리자용."""
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(username=username, tenant__isnull=True).first()


def user_get_by_tenant_username(tenant, display_username: str) -> Optional[Any]:
    """테넌트별 유저 조회. 내부 저장형 t{tenant_id}_{display} 만 사용. 레거시 형식은 normalize_user_tenant_usernames 로 정규화 후 제거."""
    from django.contrib.auth import get_user_model
    from apps.core.models.user import user_internal_username
    if not tenant or not (display_username or "").strip():
        return None
    internal = user_internal_username(tenant, display_username)
    return get_user_model().objects.filter(tenant=tenant, username=internal).first()


def user_list_by_tenant_login_identifier(tenant, display_username: str) -> list[Any]:
    """List login identities with an active membership in the requested tenant.

    ``User.tenant`` is the default tenant pointer, not an authorization source.
    A user with legitimate memberships in multiple tenants must therefore be
    discoverable from any authorized tenant. Password validation at the auth
    boundary disambiguates legacy display-identifier collisions.
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Q
    from apps.core.models.user import user_display_username

    raw = (display_username or "").strip()
    if not tenant or not raw:
        return []

    candidates = list(
        get_user_model().objects
        .filter(
            tenant_memberships__tenant=tenant,
            tenant_memberships__is_active=True,
        )
        .filter(Q(username=raw) | Q(username__endswith=f"_{raw}"))
        .select_related("tenant")
        .distinct()
    )
    return [user for user in candidates if user_display_username(user) == raw]


def user_get_or_create(username: str, defaults: dict) -> tuple[Any, bool]:
    from django.contrib.auth import get_user_model
    return get_user_model().objects.get_or_create(username=username, defaults=defaults)


# ---------------------------------------------------------------------------
# TenantMembership
# ---------------------------------------------------------------------------


def membership_get(tenant, user, is_active: bool = True) -> Optional[Any]:
    from apps.core.models import TenantMembership
    return (
        TenantMembership.objects
        .filter(tenant=tenant, user=user, is_active=is_active)
        .only("role")
        .first()
    )


def membership_exists(tenant, user, is_active: bool = True) -> bool:
    from apps.core.models import TenantMembership
    return TenantMembership.objects.filter(
        tenant=tenant,
        user=user,
        is_active=is_active,
    ).exists()


def membership_exists_staff(tenant, user, staff_roles: tuple = ("owner", "admin", "staff", "teacher")) -> bool:
    from apps.core.models import TenantMembership
    return TenantMembership.objects.filter(
        tenant=tenant,
        user=user,
        is_active=True,
        role__in=staff_roles,
    ).exists()


def membership_get_for_update(tenant, user) -> Optional[Any]:
    from apps.core.models import TenantMembership
    return TenantMembership.objects.select_for_update().filter(tenant=tenant, user=user).first()


def membership_get_full(tenant, user) -> Optional[Any]:
    from apps.core.models import TenantMembership
    return TenantMembership.objects.filter(tenant=tenant, user=user).first()


def membership_list_active_for_user(user) -> list[Any]:
    from apps.core.models import TenantMembership
    return list(
        TenantMembership.objects.select_for_update(of=("self",))
        .filter(user=user, is_active=True, tenant__is_active=True)
        .select_related("tenant")
        .order_by("tenant_id", "id")
    )


def student_profile_exists_active(tenant, user) -> bool:
    from apps.domains.students.models import Student
    return Student.objects.filter(
        tenant=tenant,
        user=user,
        deleted_at__isnull=True,
    ).exists()


def parent_profile_exists(tenant, user) -> bool:
    from apps.domains.parents.models import Parent
    return Parent.objects.filter(tenant=tenant, user=user).exists()


def membership_ensure_active(
    tenant,
    user,
    role: str,
    *,
    protected_existing_roles: tuple[str, ...] = (),
) -> Any:
    from apps.core.models import TenantMembership
    from django.contrib.auth import get_user_model
    from django.db import transaction

    role = str(role).strip().lower()
    allowed = {c[0] for c in TenantMembership.ROLE_CHOICES}
    if role not in allowed:
        raise ValueError(f"invalid role: {role}")
    with transaction.atomic():
        locked_user = get_user_model().objects.select_for_update().get(pk=user.pk)
        obj = (
            TenantMembership.objects.select_for_update()
            .filter(tenant=tenant, user=locked_user)
            .first()
        )
        if obj:
            if obj.role in protected_existing_roles and obj.role != role:
                raise ValueError(
                    f"protected membership role cannot change: {obj.role}"
                )
            changed = []
            if obj.role != role:
                obj.role = role
                changed.append("role")
            if not obj.is_active:
                obj.is_active = True
                changed.append("is_active")
            if changed:
                obj.save(update_fields=changed)
            return obj
        return TenantMembership.objects.create(
            tenant=tenant,
            user=locked_user,
            role=role,
            is_active=True,
        )


# ---------------------------------------------------------------------------
# Parent (apps.domains.parents)
# ---------------------------------------------------------------------------


def parent_get_by_user(user) -> Optional[Any]:
    from apps.domains.parents.models import Parent
    return Parent.objects.filter(user=user).first()


def parent_get_by_tenant_phone(tenant, phone: str) -> Optional[Any]:
    """테넌트 + 전화번호로 학부모 조회. 로그인 ID=학부모 전화번호용."""
    from apps.domains.parents.models import Parent
    raw = (phone or "").strip().replace("-", "").replace(" ", "")
    if not raw:
        return None
    return Parent.objects.filter(tenant=tenant, phone=raw).select_related("user").first()
