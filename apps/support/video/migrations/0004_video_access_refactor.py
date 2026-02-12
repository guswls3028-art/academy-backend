# Generated migration for VideoPermission -> VideoAccess refactor (SSOT)
# - Renames model in Django state only (keeps DB table video_videopermission)
# - Adds proctored_completed_at field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0003_add_access_mode_field"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameModel(
                    old_name="VideoPermission",
                    new_name="VideoAccess",
                ),
                migrations.AlterModelTable(
                    name="videoaccess",
                    table="video_videopermission",
                ),
            ],
            database_operations=[],
        ),
        migrations.AddField(
            model_name="videoaccess",
            name="proctored_completed_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When the monitored class-substitute watch was completed (auto-upgrade to FREE_REVIEW)",
            ),
        ),
    ]
