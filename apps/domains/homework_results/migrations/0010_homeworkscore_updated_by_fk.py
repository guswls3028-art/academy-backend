# Convert HomeworkScore.updated_by_user_id to a nullable User ForeignKey.
# DB column remains unchanged via db_column.

import django.db.models.deletion
from django.db import migrations, models


def cleanup_invalid_updated_by_users(apps, schema_editor):
    HomeworkScore = apps.get_model("homework_results", "HomeworkScore")
    User = apps.get_model("core", "User")

    valid_user_ids = set(User.objects.values_list("id", flat=True))
    invalid = (
        HomeworkScore.objects
        .exclude(updated_by_user_id__isnull=True)
        .exclude(updated_by_user_id__in=valid_user_ids)
    )
    count = invalid.update(updated_by_user_id=None)
    if count:
        print(f"  Cleared {count} invalid HomeworkScore updated_by_user_id values")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_pending_password_reset"),
        ("homework_results", "0009_remove_homework_homework_re_session_c13723_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(cleanup_invalid_updated_by_users, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="homeworkscore",
            name="updated_by_user_id",
            field=models.ForeignKey(
                to="core.User",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                db_column="updated_by_user_id",
                related_name="updated_homework_scores",
            ),
        ),
        migrations.RenameField(
            model_name="homeworkscore",
            old_name="updated_by_user_id",
            new_name="updated_by_user",
        ),
    ]
