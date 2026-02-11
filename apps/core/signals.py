# PATH: apps/core/signals.py
from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import Tenant, TenantDomain, Program


def _normalize_host(host: str) -> str:
    v = str(host or "").strip().lower()
    if not v:
        return ""
    return v.split(":")[0].strip()


@receiver(post_save, sender=Tenant)
def bootstrap_tenant_core_rows(sender, instance: Tenant, created: bool, **kwargs):
    if not created:
        return

    host = _normalize_host(instance.code)

    with transaction.atomic():
        Program.objects.get_or_create(
            tenant=instance,
            defaults={
                "display_name": "HakwonPlus",
                "brand_key": "hakwonplus",
                "login_variant": Program.LoginVariant.HAKWONPLUS,
                "plan": Program.Plan.PREMIUM,
                "feature_flags": {
                    "student_app_enabled": True,
                    "admin_enabled": True,
                    "attendance_hourly_rate": 15000,
                },
                "ui_config": {
                    "login_title": "HakwonPlus 관리자 로그인",
                    "login_subtitle": "",
                },
                "is_active": True,
            },
        )

        if host:
            TenantDomain.objects.get_or_create(
                host=host,
                defaults={
                    "tenant": instance,
                    "is_primary": True,
                    "is_active": True,
                },
            )
