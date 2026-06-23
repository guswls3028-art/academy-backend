# Convert Submission.enrollment_id to a nullable Enrollment ForeignKey.
# DB column remains unchanged via db_column.

import django.db.models.deletion
from django.db import migrations, models


def normalize_submission_enrollment_refs(apps, schema_editor):
    Submission = apps.get_model("submissions", "Submission")
    Enrollment = apps.get_model("enrollment", "Enrollment")
    db_alias = schema_editor.connection.alias

    valid_enrollment_ids = Enrollment.objects.using(db_alias).values("id")
    invalid = (
        Submission.objects.using(db_alias)
        .exclude(enrollment_id__isnull=True)
        .exclude(enrollment_id__in=valid_enrollment_ids)
    )
    count = invalid.update(enrollment_id=None)
    if count:
        print(f"  Cleared {count} invalid Submission enrollment_id values")


class Migration(migrations.Migration):

    dependencies = [
        ("enrollment", "0001_initial"),
        ("submissions", "0007_omr_fact_fk_conversion"),
    ]

    operations = [
        migrations.RunPython(normalize_submission_enrollment_refs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="submission",
            name="enrollment_id",
            field=models.ForeignKey(
                to="enrollment.Enrollment",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                db_column="enrollment_id",
                related_name="submissions",
            ),
        ),
        migrations.RenameField(
            model_name="submission",
            old_name="enrollment_id",
            new_name="enrollment",
        ),
        migrations.RemoveIndex(
            model_name="submission",
            name="submissions_enrollm_bf0086_idx",
        ),
        migrations.AddIndex(
            model_name="submission",
            index=models.Index(fields=["enrollment", "created_at"], name="submissions_enrollm_bf0086_idx"),
        ),
    ]
