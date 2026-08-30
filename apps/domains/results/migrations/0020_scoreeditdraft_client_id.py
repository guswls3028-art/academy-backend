from django.db import migrations, models


ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "Relax the score-draft uniqueness from one row per user to one row per browser "
    "client; the database default keeps older API inserts compatible during overlap."
)


def backfill_client_ids(apps, schema_editor):
    ScoreEditDraft = apps.get_model("results", "ScoreEditDraft")
    for draft in ScoreEditDraft.objects.only("id", "payload", "client_id").iterator():
        payload = draft.payload
        client_id = ""
        if isinstance(payload, dict):
            client_id = str(payload.get("client_id") or "")[:128]
        if client_id:
            ScoreEditDraft.objects.filter(id=draft.id).update(client_id=client_id)


class Migration(migrations.Migration):

    dependencies = [
        ("results", "0019_wrongnotepdf_source_selection"),
    ]

    operations = [
        migrations.AddField(
            model_name="scoreeditdraft",
            name="client_id",
            field=models.CharField(db_default="", default="", max_length=128),
        ),
        migrations.RunPython(backfill_client_ids, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name="scoreeditdraft",
            unique_together={
                ("tenant", "session", "editor_user", "client_id"),
            },
        ),
    ]
