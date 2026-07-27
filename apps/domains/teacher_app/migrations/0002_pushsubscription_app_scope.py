from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teacher_app", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="pushsubscription",
            name="app_scope",
            field=models.CharField(
                choices=[("teacher", "Teacher"), ("platform", "Platform")],
                db_index=True,
                default="teacher",
                max_length=16,
            ),
        ),
        migrations.AddIndex(
            model_name="pushsubscription",
            index=models.Index(
                fields=["tenant", "app_scope", "is_active"],
                name="teacher_push_scope_idx",
            ),
        ),
    ]
