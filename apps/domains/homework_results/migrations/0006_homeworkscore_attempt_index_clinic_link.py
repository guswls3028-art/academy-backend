# Generated migration: HomeworkScore attempt_index + clinic_link

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("homework_results", "0005_fk_conversion"),
        ("progress", "0005_cliniclink_source_fields"),
    ]

    operations = [
        # 1. Add attempt_index (default=1 for all existing rows)
        migrations.AddField(
            model_name="homeworkscore",
            name="attempt_index",
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text="시도 차수: 1=1차(성적 산출), 2+=클리닉 재시도",
            ),
        ),
        # 2. Add clinic_link FK
        migrations.AddField(
            model_name="homeworkscore",
            name="clinic_link",
            field=models.ForeignKey(
                blank=True,
                help_text="클리닉 재시도 시 연결된 ClinicLink (attempt_index>=2)",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="homework_retake_scores",
                to="progress.cliniclink",
            ),
        ),
        # 3. Remove old unique constraint
        migrations.RemoveConstraint(
            model_name="homeworkscore",
            name="uniq_hwscore_enrollment_session_homework",
        ),
        # 4. Add new unique constraint with attempt_index
        migrations.AddConstraint(
            model_name="homeworkscore",
            constraint=models.UniqueConstraint(
                fields=["enrollment", "session", "homework", "attempt_index"],
                name="uniq_hwscore_enroll_sess_hw_attempt",
            ),
        ),
    ]
