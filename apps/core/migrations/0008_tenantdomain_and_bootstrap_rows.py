# PATH: apps/core/migrations/0008_tenantdomain_and_bootstrap_rows.py
from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion


def backfill_tenantdomain_and_program(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    TenantDomain = apps.get_model("core", "TenantDomain")
    Program = apps.get_model("core", "Program")

    def normalize_host(host: str) -> str:
        v = str(host or "").strip().lower()
        if not v:
            return ""
        return v.split(":")[0].strip()

    tenants = Tenant.objects.all().order_by("id")
    for t in tenants:
        # Program backfill
        if not Program.objects.filter(tenant=t).exists():
            Program.objects.create(
                tenant=t,
                display_name="HakwonPlus",
                brand_key="hakwonplus",
                login_variant="hakwonplus",
                feature_flags={
                    "student_app_enabled": True,
                    "admin_enabled": True,
                    "attendance_hourly_rate": 15000,
                },
                ui_config={
                    "login_title": "HakwonPlus 관리자 로그인",
                    "login_subtitle": "",
                },
                is_active=True,
            )

        # TenantDomain backfill (primary)
        host = normalize_host(getattr(t, "code", ""))
        if host and not TenantDomain.objects.filter(host=host).exists():
            TenantDomain.objects.create(
                tenant=t,
                host=host,
                is_primary=True,
                is_active=True,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_rename_core_program_tenant__idx_core_progra_tenant__551fe8_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="TenantDomain",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "host",
                    models.CharField(
                        help_text="도메인/호스트(포트 제외, 소문자). 예: example.com, academy.example.com",
                        max_length=255,
                        unique=True,
                    ),
                ),
                (
                    "is_primary",
                    models.BooleanField(
                        default=True,
                        help_text="대표 도메인 여부. 일반적으로 1개만 True 권장.",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, help_text="운영 중인 도메인만 resolve 대상"),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="domains",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["host"], name="core_tenantd_host_7f8e3a_idx"),
                    models.Index(fields=["tenant", "is_active"], name="core_tenantd_tenant__c4b2d7_idx"),
                ],
            },
        ),
        migrations.RunPython(backfill_tenantdomain_and_program, migrations.RunPython.noop),
    ]
