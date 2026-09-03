from django.db import migrations


TRIGGER = "clinic_check_out"


def provision_clinic_checkout(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    MessageTemplate = apps.get_model("messaging", "MessageTemplate")
    AutoSendConfig = apps.get_model("messaging", "AutoSendConfig")

    for tenant in Tenant.objects.all().iterator():
        template, _ = MessageTemplate.objects.get_or_create(
            tenant=tenant,
            name=f"[{tenant.name or '학원'}] 클리닉 하원 알림",
            defaults={
                "category": "clinic",
                "subject": "클리닉에서 하원하였습니다",
                "body": "클리닉에서 하원하였습니다.",
                "is_system": True,
            },
        )
        config, created = AutoSendConfig.objects.get_or_create(
            tenant=tenant,
            trigger=TRIGGER,
            defaults={
                "template": template,
                "enabled": True,
                "message_mode": "alimtalk",
                "minutes_before": None,
            },
        )
        if not created and config.template_id is None:
            config.template = template
            config.save(update_fields=["template", "updated_at"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0039_merge_clinic_checkout_and_observers"),
    ]

    operations = [
        migrations.RunPython(provision_clinic_checkout, noop_reverse),
    ]
