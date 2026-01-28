from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("homework", "0002_homeworkenrollment"),
        ("homework_results", "0002_homework_and_more"),
        ("lectures", "0002_remove_session_exam"),
    ]

    operations = [
        migrations.CreateModel(
            name="HomeworkAssignment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("enrollment_id", models.IntegerField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "homework",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assignments",
                        to="homework_results.homework",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="lectures.session",
                        db_index=True,
                    ),
                ),
            ],
            options={
                "db_table": "homework_assignment",
            },
        ),
        migrations.AddConstraint(
            model_name="homeworkassignment",
            constraint=models.UniqueConstraint(
                fields=("homework", "enrollment_id"),
                name="uniq_homework_assignment_homework_enrollment",
            ),
        ),
    ]
