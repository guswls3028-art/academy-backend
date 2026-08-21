from django.contrib.postgres.operations import NotInTransactionMixin
from django.db import migrations, models


class AddIndexConcurrentlyOnPostgres(NotInTransactionMixin, migrations.AddIndex):
    """Keep SQLite migrations portable while avoiding PostgreSQL write locks."""

    atomic = False

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        if schema_editor.connection.vendor == "postgresql":
            self._ensure_not_in_transaction(schema_editor)
            schema_editor.add_index(model, self.index, concurrently=True)
            return
        schema_editor.add_index(model, self.index)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        if schema_editor.connection.vendor == "postgresql":
            self._ensure_not_in_transaction(schema_editor)
            schema_editor.remove_index(model, self.index, concurrently=True)
            return
        schema_editor.remove_index(model, self.index)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("core", "0056_alter_tenant_messaging_provider"),
    ]

    operations = [
        AddIndexConcurrentlyOnPostgres(
            model_name="opsauditlog",
            index=models.Index(
                fields=["target_user", "-created_at"],
                name="ops_audit_l_target_idx",
            ),
        ),
    ]
