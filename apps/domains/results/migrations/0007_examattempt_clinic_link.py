# Generated migration: ExamAttempt clinic_link FK

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("results", "0006_fk_conversion"),
        ("progress", "0005_cliniclink_source_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="examattempt",
            name="clinic_link",
            field=models.ForeignKey(
                blank=True,
                help_text="클리닉 재시험 시 연결된 ClinicLink (attempt_index>=2)",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="exam_retake_attempts",
                to="progress.cliniclink",
            ),
        ),
    ]
