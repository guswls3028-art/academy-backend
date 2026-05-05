# PATH: apps/domains/matchup/migrations/0013_add_vector_columns.py
# Plan B Step 2 — pgvector 컬럼 추가 (jsonb embedding 병행 운영).
#
# 운영 안전:
#   - 신규 NULL 컬럼 추가 = 즉시, 데이터 변경 0, downtime 0.
#   - 기존 jsonb embedding/image_embedding 컬럼은 유지 (cutover 검증 후 별도 제거).
#   - INDEX는 backfill 완료 후 별도 마이그레이션(0014) 에서 생성.
#
# 차원 (운영 실측, 2026-05-05):
#   - embedding (text):       384 (multilingual-e5-small)
#   - image_embedding (CLIP): 512

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0012_alter_matchupdocument_author_and_more"),
    ]

    operations = [
        # extension 활성화 (idempotent — 이미 활성화돼 있어도 안전).
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS vector",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql=(
                "ALTER TABLE matchup_matchupproblem "
                "ADD COLUMN IF NOT EXISTS embedding_v vector(384)"
            ),
            reverse_sql="ALTER TABLE matchup_matchupproblem DROP COLUMN IF EXISTS embedding_v",
        ),
        migrations.RunSQL(
            sql=(
                "ALTER TABLE matchup_matchupproblem "
                "ADD COLUMN IF NOT EXISTS image_embedding_v vector(512)"
            ),
            reverse_sql="ALTER TABLE matchup_matchupproblem DROP COLUMN IF EXISTS image_embedding_v",
        ),
    ]
