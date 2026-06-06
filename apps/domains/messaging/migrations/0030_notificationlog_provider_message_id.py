from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0029_notificationlog_target_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationlog",
            name="provider_message_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Solapi group/message id returned by the provider",
                max_length=128,
            ),
        ),
    ]
