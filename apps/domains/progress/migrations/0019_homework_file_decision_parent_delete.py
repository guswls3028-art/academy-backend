"""Let a rolling or rollback image delete parents before its ORM knows about file decisions."""

from django.db import migrations


ACADEMY_MIGRATION_PHASE = "expand"


def set_parent_deletion(apps, schema_editor, *, cascade):
    model = apps.get_model("progress", "HomeworkFileAcceptance")
    connection = schema_editor.connection
    quote = schema_editor.quote_name
    table = model._meta.db_table
    if connection.vendor == "postgresql":
        changes = []
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            constraints = connection.introspection.get_constraints(cursor, table)

    for field_name in ("tenant", "enrollment", "session", "homework"):
        field = model._meta.get_field(field_name)
        parent = field.related_model._meta.db_table
        target = field.target_field.column
        if connection.vendor == "postgresql":
            names = [
                name for name, info in constraints.items()
                if info["columns"] == [field.column] and info["foreign_key"] == (parent, target)
            ]
            if len(names) != 1:
                raise RuntimeError(f"Expected one homework file decision foreign key for {field_name}.")
            name = quote(names[0])
            action = "CASCADE" if cascade else "NO ACTION"
            changes.append(
                f"DROP CONSTRAINT {name}, ADD CONSTRAINT {name} "
                f"FOREIGN KEY ({quote(field.column)}) REFERENCES {quote(parent)} ({quote(target)}) "
                f"ON DELETE {action} DEFERRABLE INITIALLY DEFERRED"
            )
        elif connection.vendor == "sqlite":
            # SQLite cannot alter an existing FK. Keep it and delete its children before the check.
            trigger = quote(f"homework_file_decision_delete_{field_name}")
            if cascade:
                schema_editor.execute(
                    f"CREATE TRIGGER {trigger} BEFORE DELETE ON {quote(parent)} FOR EACH ROW BEGIN "
                    f"DELETE FROM {quote(table)} WHERE {quote(field.column)} = OLD.{quote(target)}; END"
                )
            else:
                schema_editor.execute(f"DROP TRIGGER {trigger}")
        else:
            raise NotImplementedError(f"Unsupported homework file decision database: {connection.vendor}")
    if connection.vendor == "postgresql":
        schema_editor.execute(f"ALTER TABLE {quote(table)} " + ", ".join(changes))


def enable_parent_deletion(apps, schema_editor):
    set_parent_deletion(apps, schema_editor, cascade=True)


def restore_orm_deletion(apps, schema_editor):
    set_parent_deletion(apps, schema_editor, cascade=False)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("progress", "0018_homework_file_decision")]
    operations = [migrations.RunPython(enable_parent_deletion, restore_orm_deletion)]
