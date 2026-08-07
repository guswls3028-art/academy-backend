from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("homework_results", "0012_homework_source_exam"),
    ]

    operations = [
        migrations.AddField(
            model_name="homework",
            name="grading_mode",
            field=models.CharField(
                choices=[("SCORE", "점수형"), ("COMPLETION", "완료형")],
                db_default="SCORE",
                default="SCORE",
                help_text="SCORE는 수치 점수, COMPLETION은 완료/미완료(1/0)로 기록한다.",
                max_length=20,
            ),
        ),
    ]
