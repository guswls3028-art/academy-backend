# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lectures", "0003_add_tenant_to_lecture"),
    ]

    operations = [
        migrations.AddField(
            model_name="lecture",
            name="lecture_time",
            field=models.CharField(blank=True, help_text="강의 시간 (예: 월수금 14:00~16:00)", max_length=100),
        ),
    ]
