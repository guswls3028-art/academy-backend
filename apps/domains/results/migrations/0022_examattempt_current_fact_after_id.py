from django.db import migrations, models

ACADEMY_MIGRATION_PHASE = "expand"
ACADEMY_MIGRATION_REASON = "Preserve immutable OMR attribution history while separating current grading facts."


class Migration(migrations.Migration):
    dependencies = [("results", "0021_parent_assessment_comment")]

    operations = [
        migrations.AddField(
            model_name="examattempt",
            name="current_fact_after_id",
            field=models.PositiveBigIntegerField(default=0, db_default=0),
        ),
    ]
