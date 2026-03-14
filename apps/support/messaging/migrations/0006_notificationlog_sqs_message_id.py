"""Add sqs_message_id to NotificationLog for DB-level dedup."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0005_add_password_find_reset_triggers"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationlog",
            name="sqs_message_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="SQS MessageId for dedup (empty for legacy logs)",
                max_length=128,
            ),
        ),
    ]
