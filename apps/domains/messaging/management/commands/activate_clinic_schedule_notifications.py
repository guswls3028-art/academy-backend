from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Tenant
from apps.domains.messaging.alimtalk_content_builders import (
    get_solapi_template_id,
    get_template_type,
)
from apps.domains.messaging.default_templates import get_default_templates
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate


TARGET_TRIGGERS = (
    "clinic_reservation_created",
    "clinic_reservation_changed",
    "clinic_cancelled",
)


class Command(BaseCommand):
    help = (
        "Enable only clinic reservation create/change/cancel Alimtalk configs for "
        "active tenants whose tenant-wide messaging switch is already on."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist the exact three clinic schedule triggers. Default is read-only.",
        )

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        tenants = list(
            Tenant.objects.filter(is_active=True, messaging_is_active=True).order_by("id")
        )
        self._preflight(tenants)

        if not apply:
            self._write_plan(tenants)
            self.stdout.write(
                self.style.WARNING(
                    "DRY-RUN: 변경 없음. 적용하려면 --apply를 사용하세요."
                )
            )
            return

        stats = {
            "tenants": len(tenants),
            "configs_created": 0,
            "configs_enabled": 0,
            "templates_created": 0,
            "templates_linked": 0,
            "unchanged": 0,
        }
        with transaction.atomic():
            locked_tenants = list(
                Tenant.objects.select_for_update()
                .filter(
                    id__in=[tenant.id for tenant in tenants],
                    is_active=True,
                    messaging_is_active=True,
                )
                .order_by("id")
            )
            self._preflight(locked_tenants)
            for tenant in locked_tenants:
                definitions = get_default_templates(tenant.name or "학원")
                for trigger in TARGET_TRIGGERS:
                    definition = definitions[trigger]
                    config = (
                        AutoSendConfig.objects.select_for_update()
                        .filter(tenant=tenant, trigger=trigger)
                        .select_related("template")
                        .first()
                    )
                    if config and (config.message_mode or "alimtalk").strip().lower() != "alimtalk":
                        raise CommandError(
                            "비알림톡 설정이 있어 전체 적용을 중단합니다: "
                            f"tenant={tenant.id} trigger={trigger} mode={config.message_mode!r}"
                        )
                    template = config.template if config else None
                    if template is None:
                        template, created = MessageTemplate.objects.get_or_create(
                            tenant=tenant,
                            name=definition["name"],
                            defaults={
                                "category": definition["category"],
                                "subject": definition.get("subject", ""),
                                "body": definition["body"],
                                "is_system": True,
                            },
                        )
                        stats["templates_created"] += int(created)

                    if config is None:
                        AutoSendConfig.objects.create(
                            tenant=tenant,
                            trigger=trigger,
                            template=template,
                            enabled=True,
                            message_mode="alimtalk",
                            minutes_before=definition.get("minutes_before"),
                        )
                        stats["configs_created"] += 1
                        continue

                    update_fields = []
                    if config.template_id is None:
                        config.template = template
                        update_fields.append("template")
                        stats["templates_linked"] += 1
                    if not config.enabled:
                        config.enabled = True
                        update_fields.append("enabled")
                        stats["configs_enabled"] += 1
                    if update_fields:
                        update_fields.append("updated_at")
                        config.save(update_fields=update_fields)
                    else:
                        stats["unchanged"] += 1

        self.stdout.write(
            self.style.SUCCESS(
                "APPLIED " + " ".join(f"{key}={value}" for key, value in stats.items())
            )
        )

    def _preflight(self, tenants):
        unavailable = [
            trigger
            for trigger in TARGET_TRIGGERS
            if not get_template_type(trigger) or not get_solapi_template_id(trigger)
        ]
        if unavailable:
            raise CommandError(
                f"승인된 공용 알림톡 매핑이 없는 trigger: {sorted(unavailable)}"
            )

        invalid_modes = list(
            AutoSendConfig.objects.filter(
                tenant__in=tenants,
                trigger__in=TARGET_TRIGGERS,
            )
            .exclude(message_mode__iexact="alimtalk")
            .values_list("tenant_id", "trigger", "message_mode")
        )
        if invalid_modes:
            raise CommandError(
                f"비알림톡 설정이 있어 전체 적용을 중단합니다: {invalid_modes}"
            )

    def _write_plan(self, tenants):
        for tenant in tenants:
            definitions = get_default_templates(tenant.name or "학원")
            configs = {
                config.trigger: config
                for config in AutoSendConfig.objects.filter(
                    tenant=tenant,
                    trigger__in=TARGET_TRIGGERS,
                ).select_related("template")
            }
            for trigger in TARGET_TRIGGERS:
                config = configs.get(trigger)
                if config is None:
                    action = "create+enable"
                else:
                    changes = []
                    if config.template_id is None:
                        changes.append("link-template")
                    if not config.enabled:
                        changes.append("enable")
                    action = "+".join(changes) or "unchanged"
                template_name = definitions[trigger]["name"]
                self.stdout.write(
                    f"tenant={tenant.id}:{tenant.code} trigger={trigger} "
                    f"action={action} template={template_name!r}"
                )
