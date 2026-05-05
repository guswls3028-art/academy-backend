# PATH: apps/domains/matchup/migrations/0014_create_vector_indexes.py
# Plan B Step 4 — HNSW 인덱스 생성 (backfill 완료 후 실행).
#
# 운영 안전:
#   - HNSW 인덱스 빌드는 메모리 소요 (데이터 크기 ~2-4x). 29k rows × 384/512 dim
#     ~ 100MB raw vectors → 인덱스 ~400MB. RDS t4g.large RAM 8GB 충분.
#   - CONCURRENTLY = 빌드 중 SELECT/INSERT 동시 가능. atomic = False 필수.
#   - SQLite (CI smoke test) 환경 skip.
#   - cosine_ops 사용 (Plan A find_similar 와 동일 거리 함수).
#
# 운영 지시:
#   - 이 마이그레이션 적용 전에 backfill_pgvector 완료 권장 (인덱스 빌드 시 row 많을수록 효과 큼).
#     `python manage.py backfill_pgvector` 먼저 실행 후 migrate.

from django.db import migrations


def _create_hnsw_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS matchup_problem_emb_hnsw_idx "
        "ON matchup_matchupproblem USING hnsw (embedding_v vector_cosine_ops)"
    )
    schema_editor.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS matchup_problem_imgemb_hnsw_idx "
        "ON matchup_matchupproblem USING hnsw (image_embedding_v vector_cosine_ops)"
    )


def _drop_hnsw_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP INDEX IF EXISTS matchup_problem_emb_hnsw_idx")
    schema_editor.execute("DROP INDEX IF EXISTS matchup_problem_imgemb_hnsw_idx")


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0013_add_vector_columns"),
    ]

    # CREATE INDEX CONCURRENTLY 는 트랜잭션 외부에서 실행 필요.
    atomic = False

    operations = [
        migrations.RunPython(_create_hnsw_indexes, reverse_code=_drop_hnsw_indexes),
    ]
