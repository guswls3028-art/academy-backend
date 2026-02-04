# PATH: apps/core/migrations/0004_backfill_attendance_expense_tenant.py
from __future__ import annotations

from django.db import migrations


def backfill_core_attendance_expense_tenant(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    Attendance = apps.get_model("core", "Attendance")
    Expense = apps.get_model("core", "Expense")

    # 이미 다 채워져 있으면 끝
    if not Attendance.objects.filter(tenant__isnull=True).exists() and not Expense.objects.filter(tenant__isnull=True).exists():
        return

    # ✅ 안전 가드:
    # - 단일 활성 tenant면 자동 백필
    # - TENANT_DEFAULT_CODE(환경)로 이미 운영 기준 tenant를 고정하는 경우도 안전하지만,
    #   마이그레이션 레벨에서는 settings 접근을 안 쓰고 “DB 사실”만 본다.
    active_qs = Tenant.objects.filter(is_active=True).order_by("id")
    active_count = active_qs.count()

    if active_count == 1:
        t = active_qs.first()
        Attendance.objects.filter(tenant__isnull=True).update(tenant=t)
        Expense.objects.filter(tenant__isnull=True).update(tenant=t)
        return

    # 활성 tenant가 0개/여러개인데 tenant null 데이터가 존재하면
    # 자동 추론은 운영사고. 명시적으로 실패해서 수동 정리 유도.
    raise RuntimeError(
        "Cannot auto-backfill tenant for core.Attendance/core.Expense: "
        f"active_tenant_count={active_count}, but tenant NULL rows exist. "
        "Fix data manually (assign correct tenant) then re-run migration."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_attendance_expense_add_tenant"),
    ]

    operations = [
        migrations.RunPython(backfill_core_attendance_expense_tenant, migrations.RunPython.noop),
    ]
