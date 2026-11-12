import django.db.models.deletion
from django.db import migrations, models


ACADEMY_MIGRATION_PHASE = "expand"
ACADEMY_MIGRATION_REASON = (
    "A nullable self-reference for linked rows only. "
    "No existing rows are rewritten, and old API and worker versions may still insert unlinked videos."
)


def set_ddl_timeouts(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")


class Migration(migrations.Migration):
    dependencies = [("video", "0026_review_playback_position")]

    operations = [
        migrations.RunPython(set_ddl_timeouts, migrations.RunPython.noop),
        migrations.AddField(
            model_name="video",
            name="reused_from",
            field=models.ForeignKey(
                blank=True,
                help_text="Root upload for a linked lesson. The linked lesson owns its access and progress.",
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="reused_in",
                to="video.video",
            ),
        ),
    ]
