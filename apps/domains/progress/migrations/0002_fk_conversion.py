# Hand-written migration: Convert integer FK fields to actual ForeignKey fields
# DB columns remain unchanged (db_column= preserves original name)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0001_initial"),
        ("enrollment", "0001_initial"),
    ]

    operations = [
        # =============================================
        # 1. SessionProgress: enrollment_id → enrollment FK
        # =============================================

        # Remove old constraint before field change
        migrations.RemoveConstraint(
            model_name="sessionprogress",
            name="unique_session_progress_per_enrollment",
        ),

        migrations.AlterField(
            model_name="sessionprogress",
            name="enrollment_id",
            field=models.ForeignKey(
                to="enrollment.Enrollment",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="enrollment_id",
                related_name="session_progress_rows",
            ),
        ),
        migrations.RenameField(
            model_name="sessionprogress",
            old_name="enrollment_id",
            new_name="enrollment",
        ),

        # Re-add constraint
        migrations.AddConstraint(
            model_name="sessionprogress",
            constraint=models.UniqueConstraint(
                fields=["enrollment", "session"],
                name="unique_session_progress_per_enrollment",
            ),
        ),

        # =============================================
        # 2. LectureProgress: enrollment_id → enrollment FK
        # =============================================

        migrations.AlterField(
            model_name="lectureprogress",
            name="enrollment_id",
            field=models.ForeignKey(
                to="enrollment.Enrollment",
                on_delete=django.db.models.deletion.CASCADE,
                unique=True,
                db_column="enrollment_id",
                related_name="lecture_progress_rows",
            ),
        ),
        migrations.RenameField(
            model_name="lectureprogress",
            old_name="enrollment_id",
            new_name="enrollment",
        ),
    ]
