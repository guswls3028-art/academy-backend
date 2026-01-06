# apps/domains/results/migrations/0003_add_attempt_id_to_result_and_fact.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("results", "0002_wrongnotepdf_examattempt_submissionanswer"),
    ]

    operations = [
        migrations.AddField(
            model_name="result",
            name="attempt_id",
            field=models.PositiveIntegerField(
                null=True,
                blank=True,
                db_index=True,
                help_text="이 Result가 참조하는 대표 ExamAttempt.id",
            ),
        ),
        migrations.AddField(
            model_name="resultfact",
            name="attempt_id",
            field=models.PositiveIntegerField(
                null=True,
                blank=True,
                db_index=True,
                help_text="이 Fact를 생성한 ExamAttempt.id",
            ),
        ),
    ]
