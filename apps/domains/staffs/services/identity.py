"""Read-only identity and explicit owner setup; never a clock-in operation."""
from django.db import IntegrityError, transaction
from rest_framework.exceptions import APIException, PermissionDenied

from apps.core.models import Tenant, TenantMembership
from apps.core.permissions import can_manage_staff_payroll
from apps.domains.staffs.models import Staff, StaffWorkType, WorkType


class OwnerStaffConflict(APIException):
    status_code = 409
    default_detail = "기존 직원 연결 정보가 겹칩니다. 직원 정보를 확인해 주세요."
    default_code = "owner_staff_conflict"


def _owner(tenant, user):
    return TenantMembership.objects.filter(
        tenant=tenant, user=user, role="owner", is_active=True,
    ).exists()


def _owner_name_phone(user):
    name = (user.name or "").strip() or user.username or "원장"
    return name, (user.phone or "").strip()


def _legacy_owner_staff(tenant, user):
    """Keep the legacy exact name/phone match without adopting another account."""
    name, phone = _owner_name_phone(user)
    matches = list(Staff.objects.filter(tenant=tenant, name=name, phone=phone)[:2])
    if len(matches) > 1 or (matches and matches[0].user_id not in (None, user.pk)):
        return None, True
    return (matches[0] if matches else None), False


def staff_identity_snapshot(*, tenant, user):
    is_owner = _owner(tenant, user)
    name, phone = _owner_name_phone(user)
    payload = {
        "is_authenticated": True,
        "is_superuser": bool(user.is_superuser),
        "is_staff": bool(user.is_staff),
        "is_payroll_manager": can_manage_staff_payroll(user, tenant),
        "is_owner": is_owner,
        "owner_display_name": name if is_owner else None,
        "owner_phone": (phone or None) if is_owner else None,
        "owner_setup_required": False,
    }
    # Fresh lookup avoids a cached reverse OneToOne after setup; never steal a
    # profile belonging to a different tenant.
    profile = Staff.objects.filter(user=user).first()
    if profile and profile.tenant_id == tenant.pk:
        payload["staff_id"] = profile.pk
        assigned = list(profile.staff_work_types.filter(
            work_type__is_active=True,
        ).select_related("work_type").order_by("id"))
        payload["assigned_work_types"] = [
            {"id": item.work_type_id, "name": item.work_type.name,
             "hourly_wage": item.effective_hourly_wage} for item in assigned
        ]
        if len(assigned) == 1:
            payload["default_work_type_id"] = assigned[0].work_type_id
        # An already-linked manual staff is never automatically assigned/reset.
        return payload
    if not is_owner:
        return payload
    staff, conflict = _legacy_owner_staff(tenant, user)
    first_type = WorkType.objects.filter(tenant=tenant, is_active=True).order_by("id").first()
    payload["owner_setup_required"] = bool(
        conflict or staff is None or first_type is None or not staff.staff_work_types.exists()
    )
    if staff:
        payload["staff_id"] = staff.pk
    if first_type:
        payload["default_work_type_id"] = first_type.pk
    return payload


def ensure_owner_staff_identity(*, tenant, user):
    # Only owner setup calls share this mutex. Do not introduce User -> Staff
    # locks: password reset already locks Staff -> User. The identity/member
    # checks run again inside the transaction, not from a caller-supplied target.
    with transaction.atomic():
        # NO KEY UPDATE still serializes setup in this tenant while allowing
        # unrelated FK key-share checks, avoiding a Tenant/User FK lock cycle.
        Tenant.objects.select_for_update(no_key=True).get(pk=tenant.pk)
        if not _owner(tenant, user):
            raise PermissionDenied("대표 계정만 직원 연결을 준비할 수 있습니다.")
        snapshot = staff_identity_snapshot(tenant=tenant, user=user)
        if not snapshot["owner_setup_required"]:
            return snapshot
        staff, conflict = _legacy_owner_staff(tenant, user)
        if conflict:
            raise OwnerStaffConflict()
        if staff is None:
            name, phone = _owner_name_phone(user)
            profile = Staff.objects.filter(user=user).first()
            try:
                with transaction.atomic():
                    staff = Staff.objects.create(
                        tenant=tenant, name=name, phone=phone,
                        user=user if profile is None else None, is_manager=True,
                    )
            except IntegrityError:
                # A simultaneous setup in another tenant may win the global
                # OneToOne. Roll back just this insert and preserve that link.
                profile = Staff.objects.filter(user=user).first()
                staff, conflict = _legacy_owner_staff(tenant, user)
                if conflict:
                    raise OwnerStaffConflict() from None
                if staff is None:
                    if profile is None or profile.tenant_id == tenant.pk:
                        raise OwnerStaffConflict() from None
                    try:
                        with transaction.atomic():
                            staff = Staff.objects.create(
                                tenant=tenant, name=name, phone=phone, user=None, is_manager=True,
                            )
                    except IntegrityError:
                        raise OwnerStaffConflict() from None
        first_type = WorkType.objects.filter(tenant=tenant, is_active=True).order_by("id").first()
        if first_type is None:
            first_type = WorkType.objects.create(tenant=tenant, name="기본", base_hourly_wage=0, is_active=True)
        if not staff.staff_work_types.exists():
            StaffWorkType.objects.get_or_create(
                tenant=tenant, staff=staff, work_type=first_type, defaults={"hourly_wage": None},
            )
        return staff_identity_snapshot(tenant=tenant, user=user)
