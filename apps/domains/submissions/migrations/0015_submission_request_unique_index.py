from django.db import migrations, models


ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "The partial unique index excludes historical and old-API null request IDs. "
    "The nullable column is already atomically recorded by 0014. Build/recovery "
    "is concurrent and verifies the exact owned index definition; no data rewrite."
)
INDEX_NAME = "unique_submission_client_request"
FIELD_NAMES = ("tenant", "enrollment", "target_type", "target_id", "client_request_id")


def request_constraint():
    return models.UniqueConstraint(
        fields=FIELD_NAMES,
        condition=models.Q(client_request_id__isnull=False),
        name=INDEX_NAME,
    )


def _owned_index(model, schema_editor):
    """Return only this migration's verified definition, never a namesake."""
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        if connection.vendor == "sqlite":
            cursor.execute("SELECT type, sql FROM sqlite_master WHERE name = %s", [INDEX_NAME])
            row = cursor.fetchone()
            if row is None:
                return None
            expected = str(request_constraint().create_sql(model, schema_editor))
            def normalize(sql):
                return " ".join(str(sql).strip().rstrip(";").split())

            if row[0] != "index" or normalize(row[1]) != normalize(expected):
                raise RuntimeError("Submission request index definition mismatch")
            return {"valid": True, "ready": True, "active": False, "name": INDEX_NAME}
        if connection.vendor != "postgresql":
            raise RuntimeError("Unsupported submission request index database")
        if connection.in_atomic_block:
            raise RuntimeError("Concurrent submission index requires a non-atomic migration")
        cursor.execute(
            "SELECT c.oid, c.relnamespace, n.nspname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = %s::regclass",
            [model._meta.db_table],
        )
        table_oid, namespace_oid, schema = cursor.fetchone()
        cursor.execute("SELECT oid, relkind FROM pg_class WHERE relnamespace = %s AND relname = %s",
                       [namespace_oid, INDEX_NAME])
        index = cursor.fetchone()
        if index is None:
            return None
        if index[1] != "i":
            raise RuntimeError("Submission request index name belongs to another relation")
        cursor.execute(
            """SELECT i.indrelid AS table_oid, i.indisunique AS is_unique,
                      i.indisprimary AS is_primary, i.indisexclusion AS is_exclusion,
                      i.indnkeyatts AS keys, i.indnatts AS attributes,
                      a.amname AS method, i.indexprs IS NULL AS plain_columns,
                      pg_get_expr(i.indpred, i.indrelid) AS predicate,
                      ARRAY(SELECT pg_get_indexdef(i.indexrelid, n, true)
                            FROM generate_series(1, i.indnatts) n) AS columns,
                      i.indisvalid AS valid, i.indisready AS ready,
                      i.indoption::smallint[] AS key_options,
                      EXISTS(SELECT 1 FROM unnest(i.indclass) class_oid
                             JOIN pg_opclass op ON op.oid = class_oid
                             WHERE NOT op.opcdefault) AS nondefault_opclass,
                      EXISTS(SELECT 1 FROM generate_series(0, i.indnkeyatts - 1) n
                             JOIN pg_attribute attribute ON attribute.attrelid = i.indrelid
                                  AND attribute.attnum = i.indkey[n]
                             WHERE i.indcollation[n] <> attribute.attcollation) AS changed_collation,
                      pg_get_indexdef(i.indexrelid) AS definition,
                      c.reloptions AS options, c.reltablespace AS tablespace,
                      EXISTS(SELECT 1 FROM pg_constraint WHERE conindid = i.indexrelid) AS attached,
                      EXISTS(SELECT 1 FROM pg_stat_progress_create_index
                             WHERE index_relid = i.indexrelid) AS active
               FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
               JOIN pg_am a ON a.oid = c.relam WHERE i.indexrelid = %s""",
            [index[0]],
        )
        row = dict(zip((column[0] for column in cursor.description), cursor.fetchone()))
    columns = [model._meta.get_field(field).column for field in FIELD_NAMES]
    if not (
        row["table_oid"] == table_oid and row["is_unique"] and not row["is_primary"]
        and not row["is_exclusion"] and row["keys"] == len(columns)
        and row["attributes"] == len(columns) and row["method"] == "btree"
        and row["plain_columns"] and row["columns"] == columns
        and row["key_options"] == [0] * len(columns)
        and not row["nondefault_opclass"] and not row["changed_collation"]
        and row["predicate"] == "(client_request_id IS NOT NULL)"
        and "NULLS NOT DISTINCT" not in row["definition"]
        and not row["options"] and row["tablespace"] == 0 and not row["attached"]
    ):
        raise RuntimeError("Submission request index definition mismatch")
    row["name"] = f"{schema_editor.quote_name(schema)}.{schema_editor.quote_name(INDEX_NAME)}"
    return row


def create_request_index(apps, schema_editor):
    model = apps.get_model("submissions", "Submission")
    existing = _owned_index(model, schema_editor)
    if existing:
        if existing["active"]:
            raise RuntimeError("Submission request index build is still active; retry after it ends")
        if existing["valid"] and existing["ready"]:
            return  # The previous build finished before its recorder write failed.
        if existing["valid"]:
            raise RuntimeError("Submission request index has inconsistent readiness")
        # A failed concurrent build can leave an invalid, exact-definition index.
        # Never repair data or remove an unrelated/actively building index.
        schema_editor.execute(f"DROP INDEX CONCURRENTLY {existing['name']}")
    constraint = request_constraint()
    if schema_editor.connection.vendor == "postgresql":
        statement = str(constraint.create_sql(model, schema_editor))
        if not statement.startswith("CREATE UNIQUE INDEX "):
            raise RuntimeError("Unexpected submission request constraint SQL")
        schema_editor.execute(statement.replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX CONCURRENTLY ", 1))
    else:
        schema_editor.add_constraint(model, constraint)
    result = _owned_index(model, schema_editor)
    if not result or not result["valid"] or not result["ready"] or result["active"]:
        raise RuntimeError("Submission request index did not become valid and ready")


def remove_request_index(apps, schema_editor):
    model = apps.get_model("submissions", "Submission")
    existing = _owned_index(model, schema_editor)
    if not existing:
        return
    if existing["active"]:
        raise RuntimeError("Submission request index build is still active")
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(f"DROP INDEX CONCURRENTLY {existing['name']}")
    else:
        schema_editor.remove_constraint(model, request_constraint())


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("submissions", "0014_submission_client_request_id")]
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunPython(create_request_index, remove_request_index, atomic=False)],
        state_operations=[migrations.AddConstraint(model_name="submission", constraint=request_constraint())],
    )]
