# apps/domains/results/migrations/0002_add_exam_attempt_meta.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("results", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="examattempt",
            name="meta",
            field=models.JSONField(
                null=True,
                blank=True,
                help_text=(
                    "Attempt 단위 메타데이터. "
                    "OMR/AI 판독 정보, total_score, pass_score, "
                    "재채점 근거 등 운영/분석용 정보 저장."
                ),
            ),
        ),
    ]
