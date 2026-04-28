"""
Re-add "both" message_mode to AutoSendConfig.
Allows tenants with SMS credentials to send both alimtalk and SMS for a trigger.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0011_remove_both_message_mode"),
    ]

    operations = [
        migrations.AlterField(
            model_name="autosendconfig",
            name="message_mode",
            field=models.CharField(
                choices=[("sms", "SMS만"), ("alimtalk", "알림톡만"), ("both", "둘 다")],
                default="sms",
                max_length=20,
            ),
        ),
    ]
