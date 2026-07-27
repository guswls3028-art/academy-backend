# PATH: apps/core/signals.py
from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import LandingConsultRequest, OpsAuditLog, Program, Tenant
from academy.adapters.db.django import repositories_core as core_repo


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
        core_repo.program_get_or_create(
            instance,
            defaults={
                "display_name": "HakwonPlus",
                "brand_key": "hakwonplus",
                "login_variant": Program.LoginVariant.HAKWONPLUS,
                "plan": Program.Plan.ALL,
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
            core_repo.tenant_domain_get_or_create_by_defaults(
                host,
                defaults={
                    "tenant": instance,
                    "is_primary": True,
                    "is_active": True,
                },
            )


def _queue_platform_push(*, kind: str, item_id: int) -> None:
    from apps.core.services.platform_push import enqueue_platform_inbox

    enqueue_platform_inbox(kind=kind, item_id=item_id)


@receiver(post_save, sender=LandingConsultRequest)
def notify_new_platform_consult(
    sender,
    instance: LandingConsultRequest,
    created: bool,
    **kwargs,
):
    if not created:
        return
    owner_tenant_id = getattr(settings, "OWNER_TENANT_ID", None)
    if (
        owner_tenant_id
        and instance.tenant_id == owner_tenant_id
        and instance.source in {"promo-contact", "promo-demo"}
    ):
        _queue_platform_push(kind="contact", item_id=instance.id)


@receiver(post_save, sender=OpsAuditLog)
def notify_new_platform_incident(
    sender,
    instance: OpsAuditLog,
    created: bool,
    **kwargs,
):
    if created and instance.action == "user_incident.manual":
        _queue_platform_push(kind="incident", item_id=instance.id)
