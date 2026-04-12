# PATH: apps/domains/staffs/views/helpers.py
# 원칙: 1테넌트 = 1프로그램. 도메인(테넌트)별 완전 격리. 조회/생성/수정/삭제는 항상 request.tenant 기준.

from django.db import transaction
from django.db.models import Sum

from rest_framework.permissions import BasePermission
from rest_framework.exceptions import ValidationError

from ..models import Staff
from academy.adapters.db.django import repositories_staffs as staff_repo
from academy.adapters.db.django import repositories_core as core_repo
from apps.core.models import TenantMembership
from apps.core.permissions import is_effective_staff


def _owner_display_for_tenant(tenant, request=None):
    """테넌트 원장(owner) 표시용 딕셔너리. 직원 목록 상단 노출용."""
    if not tenant:
        return None
    # 1) TenantMembership role=owner
    m = (
        TenantMembership.objects.filter(
            tenant=tenant, role="owner", is_active=True
        )
        .select_related("user")
        .first()
    )
    if m:
        name = (getattr(m.user, "name", None) or "").strip() or m.user.username
        phone = (getattr(m.user, "phone", None) or "").strip() or None
        return {"id": None, "name": name, "phone": phone, "role": "OWNER", "is_owner": True}
    # 2) tenant.owner_name (+ tenant.phone 있으면 원장 연락처로)
    if (getattr(tenant, "owner_name", None) or "").strip():
        name = (tenant.owner_name or "").strip()
        phone = (getattr(tenant, "phone", None) or "").strip() or None
        return {"id": None, "name": name, "phone": phone, "role": "OWNER", "is_owner": True}
    # 3) 현재 사용자가 이 테넌트 owner 멤버십 보유
    if request and request.user and request.user.is_authenticated:
        from academy.adapters.db.django import repositories_core as core_repo
        if core_repo.membership_exists_staff(tenant=tenant, user=request.user, staff_roles=("owner",)):
            name = (getattr(request.user, "name", None) or "").strip() or request.user.username
            phone = (getattr(request.user, "phone", None) or "").strip() or None
            return {"id": None, "name": name, "phone": phone, "role": "OWNER", "is_owner": True}
    # 4) DB에 원장 없을 때: 이 페이지 접근 가능한 사용자(슈퍼유저/스태프/테넌트 오너)를 대표로 표시
    if request and request.user and request.user.is_authenticated:
        if is_effective_staff(request.user, tenant):
            name = (getattr(request.user, "name", None) or "").strip() or request.user.username or "원장"
            phone = (getattr(request.user, "phone", None) or "").strip() or None
            return {"id": None, "name": name, "phone": phone, "role": "OWNER", "is_owner": True}
    return None

# ===========================
# Permissions
# ===========================

def can_access_staff_management(user, tenant=None) -> bool:
    """
    직원관리 페이지 접근 권한(관리자 권한 on).
    - owner, admin, staff 역할 → True
    - teacher 역할 → Staff.is_manager 일 때만 True
    - 비용·시급 등 민감 정보는 이 권한 있는 사람만 접근.
    """
    if not user or not user.is_authenticated:
        return False
    if not tenant:
        return False
    m = core_repo.membership_get_full(tenant, user)
    # superuser/staff: 멤버십 있으면 허용, 또는 tenant_id 일치 시 허용
    if user.is_superuser or user.is_staff:
        if m and m.is_active:
            return True
        if getattr(user, "tenant_id", None) == tenant.id:
            return True
        return False
    if not m or not m.is_active:
        return False
    if m.role in ("owner", "admin", "staff"):
        return True
    if m.role == "teacher":
        profile = getattr(user, "staff_profile", None)
        return (
            profile is not None
            and getattr(profile, "tenant_id", None) == tenant.id
            and getattr(profile, "is_manager", False)
        )
    return False


class IsPayrollManager(BasePermission):
    """직원관리 페이지 접근 = 관리자 권한 on만. 비용·시급 등 민감 정보 보호."""
    def has_permission(self, request, view):
        return can_access_staff_management(request.user, getattr(request, "tenant", None))

# ===========================
# Helpers
# ===========================

def is_month_locked(staff, date):
    return staff_repo.is_month_locked(staff, date.year, date.month)


def can_manage_payroll(user, tenant=None) -> bool:
    """직원관리(관리자 권한) 접근 가능 여부. can_access_staff_management와 동일."""
    return can_access_staff_management(user, tenant)


def generate_payroll_snapshot(staff, year, month, user):
    if staff_repo.payroll_snapshot_exists_staff(staff, year, month):
        raise ValidationError("이미 급여 스냅샷이 생성된 월입니다.")

    with transaction.atomic():
        wr_qs = staff_repo.work_record_queryset_staff_date_ym(staff, year, month)
        er_qs = staff_repo.expense_record_queryset_staff_date_ym(staff, year, month, status="APPROVED")

        work_hours = wr_qs.aggregate(total=Sum("work_hours"))["total"] or 0
        work_amount = wr_qs.aggregate(total=Sum("amount"))["total"] or 0
        approved_expense_amount = er_qs.aggregate(total=Sum("amount"))["total"] or 0
        total_amount = work_amount + approved_expense_amount

        staff_repo.payroll_snapshot_create_full(
            tenant=staff.tenant,
            staff=staff,
            year=year,
            month=month,
            work_hours=work_hours,
            work_amount=work_amount,
            approved_expense_amount=approved_expense_amount,
            total_amount=total_amount,
            generated_by=user,
        )
