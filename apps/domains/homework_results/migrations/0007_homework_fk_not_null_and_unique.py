# PATH: apps/domains/homework_results/migrations/0007_homework_fk_not_null_and_unique.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("homework_results", "0006_backfill_homework_fk_first_homework"),
    ]

    operations = [
        migrations.AlterField(
            model_name="homeworkscore",
            name="homework",
            field=models.ForeignKey(
                to="homework_results.homework",
                on_delete=models.deletion.CASCADE,
                related_name="scores",
                db_index=True,
            ),
        ),
        migrations.AlterUniqueTogether(
            name="homeworkscore",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="homeworkscore",
            constraint=models.UniqueConstraint(
                fields=("enrollment_id", "session", "homework"),
                name="uniq_hwscore_enrollment_session_homework",
            ),
        ),
        migrations.AddIndex(
            model_name="homeworkscore",
            index=models.Index(
                fields=["homework", "updated_at"],
                name="hwres_homework_upd_idx",
            ),
        ),
    ]
