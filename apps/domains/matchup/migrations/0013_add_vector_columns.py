# PATH: apps/domains/matchup/migrations/0013_add_vector_columns.py
# Plan B Step 2 — pgvector 컬럼 추가 (jsonb embedding 병행 운영).
#
# 운영 안전:
#   - 신규 NULL 컬럼 추가 = 즉시, 데이터 변경 0, downtime 0.
#   - 기존 jsonb embedding/image_embedding 컬럼은 유지 (cutover 검증 후 별도 제거).
#   - SQLite (CI smoke test) 환경에서는 pgvector 미지원이라 전 작업 skip.
#   - INDEX는 backfill 완료 후 별도 마이그레이션(0014) 에서 생성.
#
# 차원 (운영 실측, 2026-05-05):
#   - embedding (text):       384 (multilingual-e5-small)
#   - image_embedding (CLIP): 512

from django.db import migrations


def _create_vector_extension_and_columns(apps, schema_editor):
    """PostgreSQL 전용 — pgvector extension + vector 컬럼 추가."""
    if schema_editor.connection.vendor != "postgresql":
        return  # SQLite (CI smoke test) 등 미지원 DB는 skip

    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS vector")
    schema_editor.execute(
        "ALTER TABLE matchup_matchupproblem "
        "ADD COLUMN IF NOT EXISTS embedding_v vector(384)"
    )
    schema_editor.execute(
        "ALTER TABLE matchup_matchupproblem "
        "ADD COLUMN IF NOT EXISTS image_embedding_v vector(512)"
    )


def _drop_vector_columns(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        "ALTER TABLE matchup_matchupproblem DROP COLUMN IF EXISTS embedding_v"
    )
    schema_editor.execute(
        "ALTER TABLE matchup_matchupproblem DROP COLUMN IF EXISTS image_embedding_v"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0012_alter_matchupdocument_author_and_more"),
    ]

    operations = [
        migrations.RunPython(
            _create_vector_extension_and_columns,
            reverse_code=_drop_vector_columns,
        ),
    ]
