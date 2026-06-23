# Convert ScoreEditDraft integer references to ForeignKey fields.
# DB columns remain unchanged via db_column.

import django.db.models.deletion
from django.db import migrations, models


def cleanup_invalid_score_edit_drafts(apps, schema_editor):
    ScoreEditDraft = apps.get_model("results", "ScoreEditDraft")
    Tenant = apps.get_model("core", "Tenant")
    Session = apps.get_model("lectures", "Session")
    User = apps.get_model("core", "User")

    valid_tenant_ids = set(Tenant.objects.values_list("id", flat=True))
    valid_session_ids = set(Session.objects.values_list("id", flat=True))
    valid_user_ids = set(User.objects.values_list("id", flat=True))

    invalid = (
        ScoreEditDraft.objects
        .exclude(tenant_id__in=valid_tenant_ids)
        | ScoreEditDraft.objects.exclude(session_id__in=valid_session_ids)
        | ScoreEditDraft.objects.exclude(editor_user_id__in=valid_user_ids)
    )
    count = invalid.count()
    if count:
        invalid.delete()
        print(f"  Deleted {count} invalid ScoreEditDraft rows")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_pending_password_reset"),
        ("lectures", "0007_session_regular_order_session_session_type_and_more"),
        ("results", "0010_fix_unique_submission_constraint_exclude_zero"),
    ]

    operations = [
        migrations.RunPython(cleanup_invalid_score_edit_drafts, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name="scoreeditdraft",
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name="scoreeditdraft",
            name="session_id",
            field=models.ForeignKey(
                to="lectures.Session",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="session_id",
                related_name="score_edit_drafts",
            ),
        ),
        migrations.RenameField(
            model_name="scoreeditdraft",
            old_name="session_id",
            new_name="session",
        ),
        migrations.AlterField(
            model_name="scoreeditdraft",
            name="tenant_id",
            field=models.ForeignKey(
                to="core.Tenant",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="tenant_id",
                related_name="score_edit_drafts",
            ),
        ),
        migrations.RenameField(
            model_name="scoreeditdraft",
            old_name="tenant_id",
            new_name="tenant",
        ),
        migrations.AlterField(
            model_name="scoreeditdraft",
            name="editor_user_id",
            field=models.ForeignKey(
                to="core.User",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="editor_user_id",
                related_name="score_edit_drafts",
            ),
        ),
        migrations.RenameField(
            model_name="scoreeditdraft",
            old_name="editor_user_id",
            new_name="editor_user",
        ),
        migrations.AlterUniqueTogether(
            name="scoreeditdraft",
            unique_together={("tenant", "session", "editor_user")},
        ),
    ]
