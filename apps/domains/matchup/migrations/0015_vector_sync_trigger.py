# PATH: apps/domains/matchup/migrations/0015_vector_sync_trigger.py
# Plan B Step 5 — 신규 problem INSERT/UPDATE 시 vector 컬럼 자동 sync 트리거.
#
# 운영 안전:
#   - jsonb embedding 변경 시 vector 컬럼 자동 갱신. application 코드 변경 불필요.
#   - 차원 mismatch 시 vector NULL 유지 (INSERT 자체 실패 안 함 — 운영 안전).
#   - BEFORE 트리거 → INSERT 한 번에 처리, 추가 query 없음.
#   - SQLite (CI smoke test) 환경 skip.

from django.db import migrations


_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION matchup_problem_sync_vector_columns()
RETURNS TRIGGER AS $body$
BEGIN
  -- text embedding (multilingual-e5-small, 384 차원)
  IF NEW.embedding IS NOT NULL THEN
    BEGIN
      IF jsonb_typeof(NEW.embedding) = 'array' AND jsonb_array_length(NEW.embedding) = 384 THEN
        NEW.embedding_v := (NEW.embedding)::text::vector(384);
      ELSE
        NEW.embedding_v := NULL;
      END IF;
    EXCEPTION WHEN OTHERS THEN
      NEW.embedding_v := NULL;
    END;
  ELSE
    NEW.embedding_v := NULL;
  END IF;

  -- image embedding (CLIP, 512 차원)
  IF NEW.image_embedding IS NOT NULL THEN
    BEGIN
      IF jsonb_typeof(NEW.image_embedding) = 'array' AND jsonb_array_length(NEW.image_embedding) = 512 THEN
        NEW.image_embedding_v := (NEW.image_embedding)::text::vector(512);
      ELSE
        NEW.image_embedding_v := NULL;
      END IF;
    EXCEPTION WHEN OTHERS THEN
      NEW.image_embedding_v := NULL;
    END;
  ELSE
    NEW.image_embedding_v := NULL;
  END IF;

  RETURN NEW;
END;
$body$ LANGUAGE plpgsql;
"""

_CREATE_TRIGGER = (
    "DROP TRIGGER IF EXISTS matchup_problem_vector_sync ON matchup_matchupproblem; "
    "CREATE TRIGGER matchup_problem_vector_sync "
    "BEFORE INSERT OR UPDATE OF embedding, image_embedding "
    "ON matchup_matchupproblem "
    "FOR EACH ROW EXECUTE FUNCTION matchup_problem_sync_vector_columns()"
)


def _install_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(_TRIGGER_FN)
    schema_editor.execute(_CREATE_TRIGGER)


def _drop_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        "DROP TRIGGER IF EXISTS matchup_problem_vector_sync ON matchup_matchupproblem"
    )
    schema_editor.execute("DROP FUNCTION IF EXISTS matchup_problem_sync_vector_columns()")


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0014_create_vector_indexes"),
    ]

    operations = [
        migrations.RunPython(_install_trigger, reverse_code=_drop_trigger),
    ]
