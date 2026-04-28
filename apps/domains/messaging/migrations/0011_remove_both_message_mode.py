"""
Remove "both" message_mode from NotificationLog and AutoSendConfig.
Convert existing "both" rows to "alimtalk".
"""

from django.db import migrations, models


def migrate_both_to_alimtalk(apps, schema_editor):
    """Update all rows with message_mode='both' to 'alimtalk'."""
    NotificationLog = apps.get_model("messaging", "NotificationLog")
    AutoSendConfig = apps.get_model("messaging", "AutoSendConfig")

    NotificationLog.objects.filter(message_mode="both").update(message_mode="alimtalk")
    AutoSendConfig.objects.filter(message_mode="both").update(message_mode="alimtalk")


def reverse_noop(apps, schema_editor):
    """No reverse — 'both' is removed permanently."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0010_add_clinic_trigger_choices"),
    ]

    operations = [
        # Data migration first (before schema change removes the choice)
        migrations.RunPython(migrate_both_to_alimtalk, reverse_noop),
        # Schema: update choices on NotificationLog.message_mode
        migrations.AlterField(
            model_name="notificationlog",
            name="message_mode",
            field=models.CharField(
                blank=True,
                choices=[("sms", "SMS"), ("alimtalk", "알림톡")],
                default="",
                help_text="발송 방식",
                max_length=20,
            ),
        ),
        # Schema: update choices on AutoSendConfig.message_mode
        migrations.AlterField(
            model_name="autosendconfig",
            name="message_mode",
            field=models.CharField(
                choices=[("sms", "SMS만"), ("alimtalk", "알림톡만")],
                default="sms",
                max_length=20,
            ),
        ),
    ]
