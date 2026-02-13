# Generated migration: MessageTemplate solapi_template_id, solapi_status

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0003_add_template_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="messagetemplate",
            name="solapi_template_id",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="messagetemplate",
            name="solapi_status",
            field=models.CharField(
                blank=True,
                default="",
                max_length=20,
                choices=[
                    ("", "미신청"),
                    ("PENDING", "검수 대기"),
                    ("APPROVED", "승인"),
                    ("REJECTED", "반려"),
                ],
            ),
        ),
    ]
