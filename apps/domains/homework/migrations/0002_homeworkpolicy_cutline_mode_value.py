# Generated manually for HomeworkPolicy cutline_mode / cutline_value

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("homework", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="homeworkpolicy",
            name="cutline_mode",
            field=models.CharField(
                choices=[("PERCENT", "퍼센트 (%)"), ("COUNT", "문항 수")],
                default="PERCENT",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="homeworkpolicy",
            name="cutline_value",
            field=models.PositiveSmallIntegerField(
                default=80,
                help_text="PERCENT: 0-100 퍼센트, COUNT: 최소 정답 문항 수(점수)",
            ),
        ),
    ]
