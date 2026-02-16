"""
Staffs 도메인 DB 조회 — .objects. 접근을 adapters 내부로 한정 (Gate 7).
"""
from __future__ import annotations


def work_month_lock_filter(tenant):
    from apps.domains.staffs.models import WorkMonthLock
    return WorkMonthLock.objects.filter(tenant=tenant)


def payroll_snapshot_filter(tenant, year, month):
    from apps.domains.staffs.models import PayrollSnapshot
    return PayrollSnapshot.objects.filter(
        tenant_id=tenant.id if hasattr(tenant, "id") else tenant,
        year=int(year),
        month=int(month),
    )


def payroll_snapshot_exists(tenant, year, month):
    return payroll_snapshot_filter(tenant, year, month).exists()


def work_record_filter(tenant, year, month):
    from apps.domains.staffs.models import WorkRecord
    return WorkRecord.objects.filter(
        tenant=tenant,
        year=int(year),
        month=int(month),
    )


def expense_record_filter(tenant, year, month):
    from apps.domains.staffs.models import ExpenseRecord
    return ExpenseRecord.objects.filter(
        tenant=tenant,
        year=int(year),
        month=int(month),
    )


def payroll_snapshot_create(tenant, year, month, generated_by):
    from apps.domains.staffs.models import PayrollSnapshot
    return PayrollSnapshot.objects.create(
        tenant=tenant,
        year=int(year),
        month=int(month),
        generated_by=generated_by,
    )


def work_type_filter(tenant):
    from apps.domains.staffs.models import WorkType
    return WorkType.objects.filter(tenant=tenant)


def work_type_all():
    """PrimaryKeyRelatedField 등용: 전체 WorkType queryset (tenant 미지정 시)."""
    from apps.domains.staffs.models import WorkType
    return WorkType.objects.all()


def work_type_empty_queryset():
    """PrimaryKeyRelatedField 기본값용 빈 queryset."""
    from apps.domains.staffs.models import WorkType
    return WorkType.objects.none()


def staff_work_type_get(tenant, staff, work_type):
    """StaffWorkType 1건 조회. 없으면 DoesNotExist."""
    from apps.domains.staffs.models import StaffWorkType
    return StaffWorkType.objects.get(tenant=tenant, staff=staff, work_type=work_type)


def staff_work_type_get_or_none(tenant, staff, work_type):
    """StaffWorkType 1건 조회. 없으면 None."""
    from apps.domains.staffs.models import StaffWorkType
    return StaffWorkType.objects.filter(tenant=tenant, staff=staff, work_type=work_type).first()


def staff_filter_tenant(tenant):
    from apps.domains.staffs.models import Staff
    return Staff.objects.filter(tenant=tenant)


def work_record_filter_staff_month(staff, year, month):
    from apps.domains.staffs.models import WorkRecord
    return WorkRecord.objects.filter(
        staff=staff,
        year=int(year),
        month=int(month),
    )


def work_record_create(tenant, staff, year, month, **kwargs):
    from apps.domains.staffs.models import WorkRecord
    return WorkRecord.objects.create(
        tenant=tenant,
        staff=staff,
        year=int(year),
        month=int(month),
        **kwargs,
    )


def staff_work_type_filter(staff):
    from apps.domains.staffs.models import StaffWorkType
    return StaffWorkType.objects.filter(staff=staff)


def expense_record_filter_staff(staff, year, month):
    from apps.domains.staffs.models import ExpenseRecord
    return ExpenseRecord.objects.filter(
        staff=staff,
        year=int(year),
        month=int(month),
    )


def work_month_lock_filter_staff_year_month(staff, year, month):
    from apps.domains.staffs.models import WorkMonthLock
    return WorkMonthLock.objects.filter(
        staff=staff,
        year=int(year),
        month=int(month),
    )


def staff_get(tenant, pk):
    from apps.domains.staffs.models import Staff
    return Staff.objects.get(id=pk, tenant=tenant)


def staff_get_by_user_tenant(tenant, user):
    """테넌트·유저 기준 직원 한 건 (멀티테넌트에서 user.staff_profile 대체)."""
    from apps.domains.staffs.models import Staff
    if not tenant or not user:
        return None
    return Staff.objects.filter(tenant=tenant, user=user).first()


def staff_exists_tenant_user(tenant, user) -> bool:
    """이 테넌트에 해당 유저 직원이 이미 있는지."""
    from apps.domains.staffs.models import Staff
    if not tenant or not user:
        return False
    return Staff.objects.filter(tenant=tenant, user=user).exists()


def staff_get_by_name_phone(name, phone):
    from apps.domains.staffs.models import Staff
    return Staff.objects.filter(name=name, phone=phone or "").first()


def work_month_lock_update_or_create(staff, year, month, defaults):
    from apps.domains.staffs.models import WorkMonthLock
    return WorkMonthLock.objects.update_or_create(
        staff=staff,
        year=year,
        month=month,
        defaults=defaults,
    )


def payroll_snapshot_filter_tenant(tenant):
    from apps.domains.staffs.models import PayrollSnapshot
    return PayrollSnapshot.objects.filter(tenant=tenant)


def get_payroll_snapshots_for_excel(tenant_id, year, month):
    """엑셀 내보내기용: select_related, order_by 포함."""
    from apps.domains.staffs.models import PayrollSnapshot
    return (
        PayrollSnapshot.objects.filter(
            tenant_id=tenant_id,
            year=int(year),
            month=int(month),
        )
        .select_related("staff", "generated_by")
        .order_by("staff__name")
    )


def is_month_locked(staff, year, month):
    from apps.domains.staffs.models import WorkMonthLock
    return WorkMonthLock.objects.filter(
        tenant=staff.tenant,
        staff=staff,
        year=year,
        month=month,
        is_locked=True,
    ).exists()


def payroll_snapshot_exists_staff(staff, year, month):
    from apps.domains.staffs.models import PayrollSnapshot
    return PayrollSnapshot.objects.filter(
        tenant=staff.tenant,
        staff=staff,
        year=year,
        month=month,
    ).exists()


def work_record_queryset_staff_date_ym(staff, year, month):
    from apps.domains.staffs.models import WorkRecord
    return WorkRecord.objects.filter(
        tenant=staff.tenant,
        staff=staff,
        date__year=year,
        date__month=month,
    )


def expense_record_queryset_staff_date_ym(staff, year, month, status="APPROVED"):
    from apps.domains.staffs.models import ExpenseRecord
    return ExpenseRecord.objects.filter(
        tenant=staff.tenant,
        staff=staff,
        date__year=year,
        date__month=month,
        status=status,
    )


def payroll_snapshot_create_full(tenant, staff, year, month, work_hours, work_amount, approved_expense_amount, total_amount, generated_by):
    from apps.domains.staffs.models import PayrollSnapshot
    return PayrollSnapshot.objects.create(
        tenant=tenant,
        staff=staff,
        year=year,
        month=month,
        work_hours=work_hours,
        work_amount=work_amount,
        approved_expense_amount=approved_expense_amount,
        total_amount=total_amount,
        generated_by=generated_by,
    )


def work_type_queryset_tenant(tenant):
    from apps.domains.staffs.models import WorkType
    return WorkType.objects.filter(tenant=tenant).order_by("name")


def staff_queryset_tenant(tenant):
    from apps.domains.staffs.models import Staff
    return (
        Staff.objects.filter(tenant=tenant)
        .select_related("user")
        .prefetch_related("staff_work_types__work_type")
        .order_by("name")
    )


def work_record_filter_open(staff):
    from apps.domains.staffs.models import WorkRecord
    return WorkRecord.objects.filter(
        staff=staff,
        tenant=staff.tenant,
        end_time__isnull=True,
    )


def work_record_create_start(staff, work_type_id, date, start_time):
    from apps.domains.staffs.models import WorkRecord
    return WorkRecord.objects.create(
        tenant=staff.tenant,
        staff=staff,
        work_type_id=work_type_id,
        date=date,
        start_time=start_time,
    )


def staff_work_type_queryset_tenant(tenant):
    from apps.domains.staffs.models import StaffWorkType
    return StaffWorkType.objects.filter(tenant=tenant).select_related("staff", "work_type")


def expense_record_queryset_tenant(tenant):
    from apps.domains.staffs.models import ExpenseRecord
    return ExpenseRecord.objects.filter(tenant=tenant).select_related("staff", "approved_by")


def work_month_lock_queryset_tenant(tenant):
    from apps.domains.staffs.models import WorkMonthLock
    return WorkMonthLock.objects.filter(tenant=tenant).select_related("staff", "locked_by")


def work_month_lock_update_or_create_defaults(tenant, staff, year, month, defaults):
    from apps.domains.staffs.models import WorkMonthLock
    return WorkMonthLock.objects.update_or_create(
        tenant=tenant,
        staff=staff,
        year=year,
        month=month,
        defaults=defaults,
    )


def payroll_snapshot_queryset_tenant(tenant):
    from apps.domains.staffs.models import PayrollSnapshot
    return PayrollSnapshot.objects.filter(tenant=tenant).select_related("staff", "generated_by")
