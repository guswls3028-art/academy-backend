from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0014_assessmentcorrection_note"),
    ]

    operations = [
        migrations.AddField(
            model_name="assessmentcorrection",
            name="source_fingerprint",
            field=models.CharField(
                blank=True,
                help_text="완료 확인 당시 시험 점수·답안 내용의 SHA-256 지문.",
                max_length=64,
                null=True,
            ),
        ),
    ]
