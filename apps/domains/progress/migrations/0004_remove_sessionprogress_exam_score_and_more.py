from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0003_progress_policy_exam_strategy_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="sessionprogress",
            name="exam_score",
        ),
        migrations.AddField(
            model_name="progresspolicy",
            name="homework_cutline_percent",
            field=models.PositiveIntegerField(
                default=80,
                help_text="Homework pass cutline (%). 예: 80",
            ),
        ),
        migrations.AddField(
            model_name="progresspolicy",
            name="homework_round_unit",
            field=models.PositiveIntegerField(
                default=5,
                help_text="Homework percent rounding unit (%). 예: 5이면 5% 단위 반올림",
            ),
        ),
    ]
