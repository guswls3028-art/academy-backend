# PATH: apps/core/migrations/0010_tenantdomain_primary_constraint.py
from __future__ import annotations

from django.db import migrations, models
from django.db.models import Q


def normalize_primary_domain_per_tenant(apps, schema_editor):
    TenantDomain = apps.get_model("core", "TenantDomain")

    # tenant별 primary가 여러 개면 가장 작은 id만 유지하고 나머지는 False로 정리
    tenant_ids = (
        TenantDomain.objects.values_list("tenant_id", flat=True)
        .distinct()
    )

    for tenant_id in tenant_ids:
        primaries = (
            TenantDomain.objects
            .filter(tenant_id=tenant_id, is_primary=True)
            .order_by("id")
        )
        if primaries.count() <= 1:
            continue

        keep = primaries.first()
        if keep is None:
            continue

        TenantDomain.objects.filter(
            tenant_id=tenant_id,
            is_primary=True,
        ).exclude(id=keep.id).update(is_primary=False)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_rename_core_tenantd_host_7f8e3a_idx_core_tenant_host_573fcb_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(normalize_primary_domain_per_tenant, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="tenantdomain",
            constraint=models.UniqueConstraint(
                fields=("tenant",),
                condition=Q(("is_primary", True)),
                name="core_tenantdomain_one_primary_per_tenant",
            ),
        ),
    ]
