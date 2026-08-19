from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

from .models import WorkRecord


def _assert_staff_tenant(*, tenant, staff) -> None:
    if not tenant or staff.tenant_id != tenant.id:
        raise PermissionDenied("직원과 테넌트가 일치하지 않습니다.")


def work_records_for_staff_range(*, tenant, staff, date_from, date_to):
    """Canonical tenant-scoped personal work-record read."""
    _assert_staff_tenant(tenant=tenant, staff=staff)
    return (
        WorkRecord.objects.filter(
            tenant=tenant,
            staff=staff,
            date__gte=date_from,
            date__lte=date_to,
        )
        .select_related("staff", "work_type")
        .order_by("-date", "-start_time")
    )


def current_work_record_for_staff(*, tenant, staff):
    """Return the only open work record for a tenant/staff pair."""
    _assert_staff_tenant(tenant=tenant, staff=staff)
    return (
        WorkRecord.objects.filter(
            tenant=tenant,
            staff=staff,
            end_time__isnull=True,
        )
        .select_related("work_type")
        .first()
    )


def open_work_records_for_tenant(*, tenant):
    if not tenant:
        raise PermissionDenied("Tenant is required.")
    return (
        WorkRecord.objects.filter(tenant=tenant, end_time__isnull=True)
        .select_related("staff", "staff__user", "work_type")
        .order_by("staff_id", "-date", "-start_time")
    )


def work_current_status(record) -> dict:
    if record is None:
        return {"status": "OFF"}

    started_at = (
        record.start_time.strftime("%H:%M:%S")
        if hasattr(record.start_time, "strftime")
        else str(record.start_time)
    )
    break_seconds = record.break_total_seconds or (record.break_minutes * 60)
    payload = {
        "status": "BREAK" if record.current_break_started_at else "WORKING",
        "work_record_id": record.id,
        "date": record.date.isoformat(),
        "started_at": started_at,
        "work_type": record.work_type_id,
        "work_type_name": record.work_type.name,
        "hourly_wage": record.resolved_hourly_wage,
        "break_minutes": record.break_minutes,
        "break_total_seconds": break_seconds,
    }
    if record.current_break_started_at:
        payload["break_started_at"] = record.current_break_started_at.isoformat()
    return payload
