from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("homework_results", "0013_homework_grading_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="homeworkscore",
            name="reviewed_submission_revision",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
