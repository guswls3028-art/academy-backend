from django.db import migrations, models

ACADEMY_MIGRATION_PHASE = "expand"
ACADEMY_MIGRATION_REASON = "Persist per-session assessment total selection without modifying existing scores."


class Migration(migrations.Migration):
    dependencies = [("lectures", "0008_lecture_display_order")]
    operations = [
        migrations.AddField(
            model_name="session", name="score_totals",
            field=models.JSONField(blank=True, default=dict, null=True),
        ),
    ]
