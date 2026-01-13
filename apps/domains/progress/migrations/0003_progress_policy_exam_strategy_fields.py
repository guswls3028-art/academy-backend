from __future__ import annotations

from django.db import migrations, connections


def _column_exists(cursor, table: str, column: str, vendor: str) -> bool:
    """
    DB vendor 별로 컬럼 존재 여부를 검사한다.
    - postgres: information_schema
    - sqlite: PRAGMA table_info
    - mysql: information_schema
    """
    if vendor == "postgresql":
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            LIMIT 1
            """,
            [table, column],
        )
        return cursor.fetchone() is not None

    if vendor == "sqlite":
        cursor.execute(f"PRAGMA table_info({table})")
        rows = cursor.fetchall() or []
        # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        return any(r[1] == column for r in rows)

    if vendor == "mysql":
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND column_name = %s
            LIMIT 1
            """,
            [table, column],
        )
        return cursor.fetchone() is not None

    # 기타 DB는 보수적으로 False 처리 (추가 필요시 확장)
    return False


def add_progress_policy_fields(apps, schema_editor):
    """
    ProgressPolicy에 아래 컬럼이 없으면 추가한다.
    - exam_aggregate_strategy (varchar(10), default 'MAX')
    - exam_pass_source (varchar(10), default 'EXAM')

    ✅ 목적:
    - 현재 models.py에는 필드가 있는데
      0001_initial에는 없어서 운영 DB에서 500이 나는 문제를 해결
    """
    vendor = schema_editor.connection.vendor
    table = "progress_progresspolicy"

    with schema_editor.connection.cursor() as cursor:
        # 1) exam_aggregate_strategy
        if not _column_exists(cursor, table, "exam_aggregate_strategy", vendor):
            if vendor == "postgresql":
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN exam_aggregate_strategy varchar(10) NOT NULL DEFAULT 'MAX';"
                )
            elif vendor == "sqlite":
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN exam_aggregate_strategy varchar(10) NOT NULL DEFAULT 'MAX';"
                )
            elif vendor == "mysql":
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN exam_aggregate_strategy varchar(10) NOT NULL DEFAULT 'MAX';"
                )

        # 2) exam_pass_source
        if not _column_exists(cursor, table, "exam_pass_source", vendor):
            if vendor == "postgresql":
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN exam_pass_source varchar(10) NOT NULL DEFAULT 'EXAM';"
                )
            elif vendor == "sqlite":
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN exam_pass_source varchar(10) NOT NULL DEFAULT 'EXAM';"
                )
            elif vendor == "mysql":
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN exam_pass_source varchar(10) NOT NULL DEFAULT 'EXAM';"
                )


class Migration(migrations.Migration):
    dependencies = [
        ("progress", "0002_exam_aggregate_fields"),
    ]

    operations = [
        # ✅ DB 스키마를 실제로 보정한다 (조건부)
        migrations.RunPython(add_progress_policy_fields, migrations.RunPython.noop),
    ]
