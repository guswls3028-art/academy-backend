# PATH: apps/domains/teachers/migrations/0003_backfill_teacher_tenant.py
from __future__ import annotations

from django.db import migrations


def backfill_teacher_tenant(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    Teacher = apps.get_model("teachers", "Teacher")

    if not Teacher.objects.filter(tenant__isnull=True).exists():
        return

    active_qs = Tenant.objects.filter(is_active=True).order_by("id")
    active_count = active_qs.count()

    if active_count == 1:
        tenant = active_qs.first()
        Teacher.objects.filter(tenant__isnull=True).update(tenant=tenant)
        return

    raise RuntimeError(
        "Cannot auto-backfill tenant for teachers.Teacher: "
        f"active_tenant_count={active_count}, but tenant NULL rows exist."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("teachers", "0002_add_teacher_tenant"),
    ]

    operations = [
        migrations.RunPython(backfill_teacher_tenant, migrations.RunPython.noop),
    ]
