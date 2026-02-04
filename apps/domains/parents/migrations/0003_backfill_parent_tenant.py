# PATH: apps/domains/parents/migrations/0003_backfill_parent_tenant.py
from __future__ import annotations

from django.db import migrations


def backfill_parent_tenant(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    Parent = apps.get_model("parents", "Parent")

    if not Parent.objects.filter(tenant__isnull=True).exists():
        return

    active_qs = Tenant.objects.filter(is_active=True).order_by("id")
    active_count = active_qs.count()

    if active_count == 1:
        tenant = active_qs.first()
        Parent.objects.filter(tenant__isnull=True).update(tenant=tenant)
        return

    raise RuntimeError(
        "Cannot auto-backfill tenant for parents.Parent: "
        f"active_tenant_count={active_count}, but tenant NULL rows exist."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("parents", "0002_add_parent_tenant"),
    ]

    operations = [
        migrations.RunPython(backfill_parent_tenant, migrations.RunPython.noop),
    ]
