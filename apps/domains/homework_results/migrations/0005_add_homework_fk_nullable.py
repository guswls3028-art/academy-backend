# PATH: apps/domains/homework_results/migrations/0005_add_homework_fk_nullable.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("homework_results", "0004_alter_homeworkscore_table"),
    ]

    operations = [
        migrations.AddField(
            model_name="homeworkscore",
            name="homework",
            field=models.ForeignKey(
                to="homework_results.homework",
                on_delete=django.db.models.deletion.CASCADE,
                null=True,
                blank=True,
                related_name="scores",
                db_index=True,
            ),
        ),
    ]
