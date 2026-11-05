from django.db import migrations, models
import django.db.models.deletion


# The migration gate classifies all ForeignKey AlterField operations as contract.
# sqlmigrate emits no DDL; old/new processes use the same FK and stored rows.
ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "State-only CASCADE to RESTRICT change emits no SQL or data rewrite; "
    "existing schema and rows remain compatible with old/new processes. "
    "Audience protection is complete only after old API processes are replaced."
)


class Migration(migrations.Migration):
    dependencies = [("community", "0022_support_request_idempotency")]

    operations = [
        migrations.AlterField(
            model_name="postmapping",
            name="node",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="post_mappings",
                to="community.scopenode",
            ),
        ),
    ]
