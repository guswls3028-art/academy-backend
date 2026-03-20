# Hand-written migration: Convert integer FK fields to actual ForeignKey fields
# DB columns remain unchanged (db_column= preserves original name)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("homework_results", "0004_homework_status_default_open"),
        ("enrollment", "0001_initial"),
    ]

    operations = [
        # =============================================
        # HomeworkScore: enrollment_id → enrollment FK
        # =============================================

        # Remove old constraint and index before field change
        migrations.RemoveConstraint(
            model_name="homeworkscore",
            name="uniq_hwscore_enrollment_session_homework",
        ),
        migrations.RemoveIndex(
            model_name="homeworkscore",
            name="hwres_enroll_upd_idx",
        ),

        migrations.AlterField(
            model_name="homeworkscore",
            name="enrollment_id",
            field=models.ForeignKey(
                to="enrollment.Enrollment",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="enrollment_id",
                related_name="homework_scores",
            ),
        ),
        migrations.RenameField(
            model_name="homeworkscore",
            old_name="enrollment_id",
            new_name="enrollment",
        ),

        # Re-add constraint and index with new field name
        migrations.AddConstraint(
            model_name="homeworkscore",
            constraint=models.UniqueConstraint(
                fields=["enrollment", "session", "homework"],
                name="uniq_hwscore_enrollment_session_homework",
            ),
        ),
        migrations.AddIndex(
            model_name="homeworkscore",
            index=models.Index(
                fields=["enrollment", "updated_at"],
                name="hwres_enroll_upd_idx",
            ),
        ),
    ]
