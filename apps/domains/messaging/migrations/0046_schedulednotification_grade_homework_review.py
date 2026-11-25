from django.db import migrations, models


def bound_postgres_ddl_wait(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")
        schema_editor.execute("SET LOCAL statement_timeout = '30s'")


class Migration(migrations.Migration):
    dependencies = [("messaging", "0045_school_report_manual_trigger")]

    operations = [
        migrations.RunPython(bound_postgres_ddl_wait, migrations.RunPython.noop),
        migrations.AddField(
            model_name="schedulednotification",
            name="grade_homework_review",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
