from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0017_sheet_omr_shape"),
    ]

    operations = [
        migrations.AddField(
            model_name="examquestion",
            name="question_kind",
            field=models.CharField(
                blank=True,
                choices=[("choice", "객관식"), ("essay", "단답형")],
                default=None,
                help_text="문항별 유형. null이면 Sheet의 기존 앞-객관식/뒤-단답형 규칙을 사용",
                max_length=10,
                null=True,
            ),
        ),
    ]
