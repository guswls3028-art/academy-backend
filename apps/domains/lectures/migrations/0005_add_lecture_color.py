# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lectures", "0004_add_lecture_time"),
    ]

    operations = [
        migrations.AddField(
            model_name="lecture",
            name="color",
            field=models.CharField(default="#3b82f6", help_text="아이콘/라벨 색상", max_length=20),
        ),
    ]
