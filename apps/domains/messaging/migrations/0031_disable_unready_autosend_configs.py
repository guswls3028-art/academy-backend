from django.db import migrations


TEMPLATE_READY_OPT_IN_TRIGGERS = (
    "video_encoding_complete",
    "matchup_report_submitted",
    "qna_answered",
    "counsel_answered",
)


def disable_unready_autosend_configs(apps, schema_editor):
    AutoSendConfig = apps.get_model("messaging", "AutoSendConfig")

    updated = 0
    configs = (
        AutoSendConfig.objects
        .select_related("template")
        .filter(enabled=True, trigger__in=TEMPLATE_READY_OPT_IN_TRIGGERS)
    )
    for config in configs:
        template = config.template
        approved = bool(
            template
            and (template.solapi_template_id or "").strip()
            and template.solapi_status == "APPROVED"
        )
        if approved:
            continue
        config.enabled = False
        config.save(update_fields=["enabled", "updated_at"])
        updated += 1

    if updated:
        print(f"\n  Disabled {updated} unready AutoSendConfig row(s)")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0030_notificationlog_provider_message_id"),
    ]

    operations = [
        migrations.RunPython(disable_unready_autosend_configs, noop_reverse),
    ]
