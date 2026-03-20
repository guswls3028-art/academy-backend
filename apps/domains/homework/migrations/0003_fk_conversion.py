# Hand-written migration: Convert integer FK fields to actual ForeignKey fields
# DB columns remain unchanged (db_column= preserves original name)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("homework", "0002_homeworkpolicy_cutline_mode_value"),
        ("enrollment", "0001_initial"),
        ("lectures", "0001_initial"),
    ]

    operations = [
        # =============================================
        # 1. HomeworkEnrollment: session_id → session FK, enrollment_id → enrollment FK
        # =============================================

        # Remove old constraint before field changes
        migrations.RemoveConstraint(
            model_name="homeworkenrollment",
            name="uniq_homework_enrollment_per_tenant",
        ),

        migrations.AlterField(
            model_name="homeworkenrollment",
            name="session_id",
            field=models.ForeignKey(
                to="lectures.Session",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="session_id",
                related_name="homework_enrollments",
            ),
        ),
        migrations.RenameField(
            model_name="homeworkenrollment",
            old_name="session_id",
            new_name="session",
        ),

        migrations.AlterField(
            model_name="homeworkenrollment",
            name="enrollment_id",
            field=models.ForeignKey(
                to="enrollment.Enrollment",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="enrollment_id",
                related_name="homework_enrollments",
            ),
        ),
        migrations.RenameField(
            model_name="homeworkenrollment",
            old_name="enrollment_id",
            new_name="enrollment",
        ),

        # Re-add constraint with new field names
        migrations.AddConstraint(
            model_name="homeworkenrollment",
            constraint=models.UniqueConstraint(
                fields=["tenant", "session", "enrollment"],
                name="uniq_homework_enrollment_per_tenant",
            ),
        ),

        # =============================================
        # 2. HomeworkAssignment: enrollment_id → enrollment FK
        # =============================================

        # Remove old constraint
        migrations.RemoveConstraint(
            model_name="homeworkassignment",
            name="uniq_homework_assignment_per_tenant",
        ),

        migrations.AlterField(
            model_name="homeworkassignment",
            name="enrollment_id",
            field=models.ForeignKey(
                to="enrollment.Enrollment",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="enrollment_id",
                related_name="homework_assignments",
            ),
        ),
        migrations.RenameField(
            model_name="homeworkassignment",
            old_name="enrollment_id",
            new_name="enrollment",
        ),

        # Re-add constraint
        migrations.AddConstraint(
            model_name="homeworkassignment",
            constraint=models.UniqueConstraint(
                fields=["tenant", "homework", "enrollment"],
                name="uniq_homework_assignment_per_tenant",
            ),
        ),
    ]
