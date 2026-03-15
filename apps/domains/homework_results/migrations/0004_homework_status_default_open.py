# Generated migration: change default status from DRAFT to OPEN
# and convert existing DRAFT homeworks to OPEN.

from django.db import migrations, models


def convert_draft_to_open(apps, schema_editor):
    Homework = apps.get_model("homework_results", "Homework")
    Homework.objects.filter(status="DRAFT").update(status="OPEN")


class Migration(migrations.Migration):

    dependencies = [
        ("homework_results", "0003_homework_display_order"),
    ]

    operations = [
        migrations.AlterField(
            model_name="homework",
            name="status",
            field=models.CharField(
                choices=[("DRAFT", "초안"), ("OPEN", "진행중"), ("CLOSED", "마감")],
                db_index=True,
                default="OPEN",
                max_length=20,
            ),
        ),
        migrations.RunPython(convert_draft_to_open, migrations.RunPython.noop),
    ]
