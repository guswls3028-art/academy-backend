from django.db import migrations, models


def bound_ddl_wait(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET LOCAL lock_timeout = '5s'")
        schema_editor.execute("SET LOCAL statement_timeout = '30s'")


class Migration(migrations.Migration):
    dependencies = [("messaging", "0046_schedulednotification_grade_homework_review")]
    operations = [
        migrations.RunPython(bound_ddl_wait, migrations.RunPython.noop),
        migrations.AddField(
            model_name="notificationlog",
            name="provider_reference_kind",
            field=models.CharField(blank=True, max_length=7, null=True),
        ),
    ]
