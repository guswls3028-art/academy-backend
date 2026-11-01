from django.db import migrations, models

# The general guard treats every unique constraint as contract work. This one
# covers only non-null values of a new nullable column: all historical rows and
# writes from old API instances remain excluded during the rolling replacement.
ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "Unique request IDs apply only to the new nullable column when non-null; "
    "historical and old-API writes remain null and unrestricted. PostgreSQL builds "
    "the index concurrently without holding a table write lock. No backfill or deletion."
)


def request_constraint():
    return models.UniqueConstraint(
        fields=("tenant", "enrollment", "target_type", "target_id", "client_request_id"),
        condition=models.Q(client_request_id__isnull=False),
        name="unique_submission_client_request",
    )


def create_request_index(apps, schema_editor):
    model = apps.get_model("submissions", "Submission")
    constraint = request_constraint()
    if schema_editor.connection.vendor != "postgresql":
        schema_editor.add_constraint(model, constraint)
        return
    if schema_editor.connection.in_atomic_block:
        raise RuntimeError("Concurrent submission index requires a non-atomic migration")
    statement = str(constraint.create_sql(model, schema_editor))
    if not statement.startswith("CREATE UNIQUE INDEX "):
        raise RuntimeError("Unexpected submission request constraint SQL")
    schema_editor.execute(statement.replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX CONCURRENTLY ", 1))


def remove_request_index(apps, schema_editor):
    model = apps.get_model("submissions", "Submission")
    constraint = request_constraint()
    if schema_editor.connection.vendor != "postgresql":
        schema_editor.remove_constraint(model, constraint)
        return
    if schema_editor.connection.in_atomic_block:
        raise RuntimeError("Concurrent submission index removal requires a non-atomic migration")
    statement = str(constraint.remove_sql(model, schema_editor))
    if not statement.startswith("DROP INDEX "):
        raise RuntimeError("Unexpected submission request constraint rollback SQL")
    schema_editor.execute(statement.replace("DROP INDEX ", "DROP INDEX CONCURRENTLY ", 1))


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("submissions", "0013_homework_media_preview")]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="client_request_id",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(create_request_index, remove_request_index, atomic=False)],
            state_operations=[migrations.AddConstraint(model_name="submission", constraint=request_constraint())],
        ),
    ]
