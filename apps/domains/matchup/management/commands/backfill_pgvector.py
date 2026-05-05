# PATH: apps/domains/matchup/management/commands/backfill_pgvector.py
# Plan B Step 3 — jsonb embedding → vector(384/512) 컬럼 backfill.
#
# 운영 안전:
#   - 배치 (default 1000) 처리. 트랜잭션 단위 작음 = lock 잠깐만 잡음.
#   - WHERE embedding_v IS NULL — 이미 backfilled row 자동 skip (재실행 안전).
#   - jsonb '[1.0, 2.0]'::text::vector 캐스트 사용. 차원 mismatch 시 cast 에러
#     → 해당 row 건너뛰고 다음 배치 진행.

from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "jsonb embedding/image_embedding → vector 컬럼 backfill."

    def add_arguments(self, parser):
        parser.add_argument("--batch", type=int, default=1000)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, batch: int, dry_run: bool, **kwargs):
        self._backfill(
            column="embedding",
            v_column="embedding_v",
            dim=384,
            batch=batch,
            dry_run=dry_run,
        )
        self._backfill(
            column="image_embedding",
            v_column="image_embedding_v",
            dim=512,
            batch=batch,
            dry_run=dry_run,
        )

    def _backfill(self, *, column: str, v_column: str, dim: int, batch: int, dry_run: bool):
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM matchup_matchupproblem "
                f"WHERE {column} IS NOT NULL AND {v_column} IS NULL"
            )
            total = cur.fetchone()[0]
            self.stdout.write(f"[{column}] todo: {total} rows (batch={batch}, dim={dim})")

            if dry_run:
                cur.execute(
                    f"SELECT id, jsonb_array_length({column}) FROM matchup_matchupproblem "
                    f"WHERE {column} IS NOT NULL AND {v_column} IS NULL "
                    f"ORDER BY id LIMIT 5"
                )
                self.stdout.write(f"[{column}] sample dim check: {cur.fetchall()}")
                return

            done = 0
            t0 = time.monotonic()
            while True:
                # 배치당 1 트랜잭션. 동일 row 동시 lock 회피 위해 SELECT FOR UPDATE SKIP LOCKED 미사용
                # (matchup callbacks 가 동시 INSERT 만 하고 UPDATE 안 함 — race 안전).
                cur.execute(
                    f"""
                    UPDATE matchup_matchupproblem
                    SET {v_column} = ({column})::text::vector
                    WHERE id IN (
                      SELECT id FROM matchup_matchupproblem
                      WHERE {column} IS NOT NULL AND {v_column} IS NULL
                        AND jsonb_array_length({column}) = %s
                      ORDER BY id
                      LIMIT %s
                    )
                    """,
                    [dim, batch],
                )
                affected = cur.rowcount
                if affected == 0:
                    break
                done += affected
                elapsed = time.monotonic() - t0
                rate = done / elapsed if elapsed > 0 else 0
                self.stdout.write(
                    f"[{column}] +{affected} (total {done}/{total}, {rate:.0f} rows/s)"
                )

            # 차원 mismatch 로 skip 된 row 확인
            cur.execute(
                f"SELECT count(*) FROM matchup_matchupproblem "
                f"WHERE {column} IS NOT NULL AND {v_column} IS NULL"
            )
            skipped = cur.fetchone()[0]
            self.stdout.write(self.style.SUCCESS(
                f"[{column}] done. backfilled={done}, skipped (dim mismatch)={skipped}"
            ))
