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


def staff_work_type_filter(staff):
    from apps.domains.staffs.models import StaffWorkType
    return StaffWorkType.objects.filter(staff=staff)


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


def staff_get_for_update(tenant_id, pk):
    """Canonical Staff mutex for open-work-record writers."""
    from apps.domains.staffs.models import Staff

    return Staff.objects.select_for_update().get(
        id=pk,
        tenant_id=tenant_id,
    )


def staff_map_for_update(tenant_id, staff_ids):
    """Lock multiple Staff mutexes in deterministic primary-key order."""
    from apps.domains.staffs.models import Staff

    ids = sorted({int(staff_id) for staff_id in staff_ids})
    rows = list(
        Staff.objects.select_for_update()
        .filter(tenant_id=tenant_id, id__in=ids)
        .order_by("id")
    )
    if len(rows) != len(ids):
        raise Staff.DoesNotExist
    return {staff.id: staff for staff in rows}


def staff_get_by_name_phone(name, phone, tenant=None):
    from apps.domains.staffs.models import Staff
    qs = Staff.objects.filter(name=name, phone=phone or "")
    if tenant is not None:
        qs = qs.filter(tenant=tenant)
    return qs.first()


def staff_id_by_name_phone_map_tenant(tenant) -> dict[tuple[str, str], int]:
    """테넌트 내 Staff의 (name, phone) → id 맵. Teacher list staff_id 룩업 N+1 회피용."""
    from apps.domains.staffs.models import Staff
    return {
        (name, phone or ""): sid
        for sid, name, phone in Staff.objects.filter(tenant=tenant).values_list("id", "name", "phone")
    }


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


def work_record_open_exists(staff, exclude_record_id=None):
    query = work_record_filter_open(staff)
    if exclude_record_id is not None:
        query = query.exclude(id=exclude_record_id)
    return query.exists()


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


def work_month_lock_get_or_create_defaults(tenant, staff, year, month, defaults):
    from apps.domains.staffs.models import WorkMonthLock

    return WorkMonthLock.objects.get_or_create(
        tenant=tenant,
        staff=staff,
        year=year,
        month=month,
        defaults=defaults,
    )


def payroll_close_blockers(staff, year, month, *, sample_limit=20):
    """Return bounded identifiers for rows that make a snapshot incomplete."""
    from django.db.models import Q

    from apps.domains.staffs.models import ExpenseRecord, WorkRecord

    work_records = WorkRecord.objects.filter(
        tenant=staff.tenant,
        staff=staff,
        date__year=year,
        date__month=month,
    )
    open_ids = list(
        work_records.filter(end_time__isnull=True)
        .order_by("id")
        .values_list("id", flat=True)[:sample_limit]
    )
    incomplete_ids = list(
        work_records.filter(end_time__isnull=False)
        .filter(
            Q(work_hours__isnull=True)
            | Q(amount__isnull=True)
            | Q(resolved_hourly_wage__isnull=True)
            | Q(current_break_started_at__isnull=False)
        )
        .order_by("id")
        .values_list("id", flat=True)[:sample_limit]
    )
    pending_expense_ids = list(
        ExpenseRecord.objects.filter(
            tenant=staff.tenant,
            staff=staff,
            date__year=year,
            date__month=month,
            status="PENDING",
        )
        .order_by("id")
        .values_list("id", flat=True)[:sample_limit]
    )
    return {
        "open_work_record_ids": open_ids,
        "incomplete_work_record_ids": incomplete_ids,
        "pending_expense_ids": pending_expense_ids,
        "sample_limit": sample_limit,
    }


def payroll_snapshot_queryset_tenant(tenant):
    from apps.domains.staffs.models import PayrollSnapshot
    return PayrollSnapshot.objects.filter(tenant=tenant).select_related("staff", "generated_by")
