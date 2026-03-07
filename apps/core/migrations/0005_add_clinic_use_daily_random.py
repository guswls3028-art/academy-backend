# Generated manually for clinic_use_daily_random

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_add_clinic_idcard_colors"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="clinic_use_daily_random",
            field=models.BooleanField(
                default=False,
                help_text="매일 자동 3색 사용 시 True",
            ),
        ),
    ]
