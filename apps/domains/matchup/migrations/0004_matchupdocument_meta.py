# Generated for MatchupDocument.meta field (segmentation_method 등 관측용)
#
# 드리프트 대응: 프로덕션 Postgres DB에는 meta 컬럼이 이미 수동으로 추가된 상태.
# SQLite 테스트 DB에는 컬럼 없음. vendor별로 idempotent하게 처리.

from django.db import migrations, models


def forwards(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    table = "matchup_matchupdocument"

    with schema_editor.connection.cursor() as cur:
        if vendor == "postgresql":
            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name=%s AND column_name='meta'",
                [table],
            )
            exists = cur.fetchone() is not None
        else:
            # sqlite
            cur.execute(f"PRAGMA table_info({table})")
            exists = any(row[1] == "meta" for row in cur.fetchall())

    if exists:
        if vendor == "postgresql":
            # DB-level default 보강 (향후 INSERT 누락 대비)
            schema_editor.execute(
                f"ALTER TABLE {table} ALTER COLUMN meta SET DEFAULT '{{}}'::jsonb"
            )
        return

    if vendor == "postgresql":
        schema_editor.execute(
            f"ALTER TABLE {table} "
            "ADD COLUMN meta jsonb NOT NULL DEFAULT '{}'::jsonb"
        )
    else:
        # sqlite — 테스트 DB
        schema_editor.execute(
            f"ALTER TABLE {table} "
            "ADD COLUMN meta TEXT NOT NULL DEFAULT '{}'"
        )


def backwards(apps, schema_editor):
    schema_editor.execute(
        "ALTER TABLE matchup_matchupdocument DROP COLUMN IF EXISTS meta"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0003_alter_matchupproblem_unique_together_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(forwards, backwards),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="matchupdocument",
                    name="meta",
                    field=models.JSONField(blank=True, default=dict),
                ),
            ],
        ),
    ]
