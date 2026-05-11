from django.db import migrations, models


def migrate_autosend_to_alimtalk(apps, schema_editor):
    AutoSendConfig = apps.get_model("messaging", "AutoSendConfig")
    AutoSendConfig.objects.exclude(message_mode="alimtalk").update(message_mode="alimtalk")


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0023_provision_community_triggers"),
    ]

    operations = [
        migrations.RunPython(migrate_autosend_to_alimtalk, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="autosendconfig",
            name="message_mode",
            field=models.CharField(
                choices=[("alimtalk", "알림톡만")],
                default="alimtalk",
                max_length=20,
            ),
        ),
    ]
