from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0013_assessmentcorrection"),
    ]

    operations = [
        migrations.AddField(
            model_name="assessmentcorrection",
            name="note",
            field=models.TextField(
                blank=True,
                default="",
                help_text="교사가 남긴 미완료 범위 또는 확인 메모.",
            ),
        ),
    ]
