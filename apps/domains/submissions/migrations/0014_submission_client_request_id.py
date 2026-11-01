from django.db import migrations, models

# The general guard treats every unique constraint as contract work. This one
# covers only non-null values of a new nullable column: all historical rows and
# writes from old API instances remain excluded during the rolling replacement.
ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "Unique request IDs apply only to the new nullable column when non-null; "
    "historical and old-API writes remain null and unrestricted. No backfill or deletion."
)


class Migration(migrations.Migration):
    dependencies = [("submissions", "0013_homework_media_preview")]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="client_request_id",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddConstraint(
            model_name="submission",
            constraint=models.UniqueConstraint(
                fields=("tenant", "enrollment", "target_type", "target_id", "client_request_id"),
                condition=models.Q(client_request_id__isnull=False),
                name="unique_submission_client_request",
            ),
        ),
    ]
