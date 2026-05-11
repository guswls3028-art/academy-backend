"""Stage 6.3S — manual cut MatchupProblem 의 image_embedding 을 raw crop 입력으로 재계산.

배경:
  matchup_manual_index.py:227 가 OCR 용 _preprocess_camera_image (CLAHE+deskew+Unsharp)
  결과를 CLIP image embedding 입력에도 그대로 사용 → manual problem image_embedding
  distribution 이 auto problem (raw crop 입력) 과 shift. cross matching 정확도 저하.

  코드 fix: matchup_manual_index.py 안에서 CLIP 입력을 raw crop 으로 분리 (Stage 6.3S 코드).
  본 backfill 은 코드 fix 이전에 생성된 manual problem 의 image_embedding 을 재계산.

운영 정책 (학원장 데이터 보호):
- dry-run default. --apply 명시해야 실제 UPDATE.
- --tenant-id 필수. 멀티테넌트 batch 격리.
- --max-rows cap (default 100, --apply 시 무제한 풀려면 --no-cap 필요).
- meta.image_embedding_backfill_v6_3s = "<timestamp>" 마커로 idempotent (재실행 안전).
- 기존 image_embedding 값을 meta.image_embedding_pre_6_3s 에 백업 (PITR 외 추가 안전망).
- selected_problem_ids / hit_report / manual=True 마커 미접근 — image_embedding 만 update.
- 1 row per transaction.atomic — 부분 실패 격리.

사용:
  python manage.py backfill_manual_clip_embedding --tenant-id 2 --max-rows 10        # dry-run
  python manage.py backfill_manual_clip_embedding --tenant-id 2 --apply --max-rows 50

회귀 안전망:
- 결과 비교 metric (cosine_sim before vs after) 로깅 — 0.5 미만이면 distribution shift 큼.
- 100 row 단위 진행률 출력. ctrl-c 안전 (transaction.atomic per row).
"""
import os
from datetime import datetime
from typing import Optional

from django.core.management.base import BaseCommand
from django.db import transaction

# tqdm progress bar (sentence-transformers 기본) → cp949 호환 안 되는 stderr 방지
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")


_BACKFILL_MARKER_KEY = "image_embedding_backfill_v6_3s"
_PRE_BACKUP_KEY = "image_embedding_pre_6_3s"


def _cosine_sim(a, b) -> Optional[float]:
    if not a or not b or len(a) != len(b):
        return None
    try:
        import numpy as np
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        na = float(np.linalg.norm(va)) or 1.0
        nb = float(np.linalg.norm(vb)) or 1.0
        return float(np.dot(va, vb) / (na * nb))
    except Exception:
        return None


class Command(BaseCommand):
    help = "manual cut MatchupProblem 의 image_embedding 을 raw crop 입력으로 재계산 (Stage 6.3S)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True,
                            help="멀티테넌트 격리 — 한 번에 한 tenant 만 처리.")
        parser.add_argument("--max-rows", type=int, default=100,
                            help="처리 row cap. 운영 안전망. --no-cap 으로 해제.")
        parser.add_argument("--no-cap", action="store_true",
                            help="--max-rows 무시. tenant 전체 backfill (대량 처리).")
        parser.add_argument("--apply", action="store_true",
                            help="실제 UPDATE 실행. 미지정 시 dry-run.")
        parser.add_argument("--rerun", action="store_true",
                            help="이미 backfill 된 row 도 재실행 (마커 무시).")
        parser.add_argument("--problem-id", type=int, action="append", default=[],
                            help="특정 problem id 만 처리 (debug 용, 여러 개 가능).")

    def handle(self, *args, **opts):
        from apps.domains.matchup.models import MatchupProblem

        tenant_id = opts["tenant_id"]
        apply_changes = bool(opts["apply"])
        rerun = bool(opts["rerun"])
        max_rows = opts["max_rows"]
        no_cap = bool(opts["no_cap"])

        # manual=True + image_key 보유 + image_embedding 채워진 row 만 대상.
        # raw crop 가져오려면 image_key 가 R2 에 있어야 함.
        qs = MatchupProblem.objects.filter(
            tenant_id=tenant_id,
        ).exclude(image_key="").exclude(image_embedding__isnull=True)
        # meta.manual=True 필터 — sqlite JSONField 호환을 위해 meta__contains 사용.
        qs = qs.filter(meta__contains={"manual": True})

        if opts["problem_id"]:
            qs = qs.filter(id__in=opts["problem_id"])

        # idempotent 마커: 이미 backfill 된 row 는 skip (rerun=True 시 무시).
        if not rerun:
            qs = qs.exclude(meta__contains={_BACKFILL_MARKER_KEY: True})

        qs = qs.only("id", "image_key", "image_embedding", "meta")
        total = qs.count()
        self.stdout.write(
            f"[6.3S backfill] tenant={tenant_id} eligible={total} "
            f"apply={apply_changes} rerun={rerun} cap={'none' if no_cap else max_rows}"
        )
        if total == 0:
            return

        if not no_cap:
            qs = qs[:max_rows]

        rows = list(qs)
        actual = len(rows)
        if not apply_changes:
            self.stdout.write(f"[dry-run] would process {actual} rows. add --apply to execute.")
            for r in rows[:10]:
                self.stdout.write(
                    f"  problem={r.id} image_key={r.image_key} "
                    f"emb_dim={len(r.image_embedding) if r.image_embedding else 0}"
                )
            if actual > 10:
                self.stdout.write(f"  ...+{actual - 10} more")
            return

        # 실제 적용 — CLIP 모델 + R2 다운로드 import 는 apply 분기 안에서만.
        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
        from academy.adapters.ai.embedding.image_service import get_image_embeddings
        from academy.adapters.ai.storage.downloader import (
            download_to_tmp,
            cleanup_tmp_for_path,
        )

        applied = 0
        skipped = 0
        failed = 0
        sim_distribution: list = []  # before↔after cosine 분포

        for idx, problem in enumerate(rows, start=1):
            try:
                url = generate_presigned_get_url_storage(key=problem.image_key, expires_in=300)
                if not url:
                    skipped += 1
                    self.stdout.write(f"  [{idx}/{actual}] problem={problem.id} skip — no presign URL")
                    continue

                local_path = download_to_tmp(
                    download_url=url,
                    job_id=f"backfill_6_3s_{problem.id}",
                )
                try:
                    batch = get_image_embeddings([local_path])
                    new_vec = batch.vectors[0] if batch.vectors else None
                finally:
                    cleanup_tmp_for_path(local_path)

                if not new_vec:
                    failed += 1
                    self.stdout.write(
                        f"  [{idx}/{actual}] problem={problem.id} fail — empty CLIP vector"
                    )
                    continue

                old_vec = list(problem.image_embedding or [])
                sim = _cosine_sim(old_vec, new_vec)

                # transaction.atomic per row — 부분 실패 격리.
                with transaction.atomic():
                    # refresh + update — 다른 worker race 가능성 최소화.
                    locked = (
                        MatchupProblem.objects
                        .select_for_update()
                        .only("id", "image_embedding", "meta")
                        .get(id=problem.id, tenant_id=tenant_id)
                    )
                    new_meta = dict(locked.meta or {})
                    # 기존 값 백업 (재실행에도 한 번만 set — 첫 backfill 의 raw 직전 값을 보존).
                    if _PRE_BACKUP_KEY not in new_meta:
                        new_meta[_PRE_BACKUP_KEY] = old_vec
                    new_meta[_BACKFILL_MARKER_KEY] = True
                    new_meta["image_embedding_backfill_at"] = datetime.utcnow().isoformat() + "Z"
                    if sim is not None:
                        new_meta["image_embedding_backfill_old_new_sim"] = round(sim, 4)
                    locked.image_embedding = new_vec
                    locked.meta = new_meta
                    locked.save(update_fields=["image_embedding", "meta", "updated_at"])

                applied += 1
                if sim is not None:
                    sim_distribution.append(sim)

                if applied % 25 == 0:
                    avg_sim = (
                        sum(sim_distribution) / len(sim_distribution)
                        if sim_distribution else float("nan")
                    )
                    self.stdout.write(
                        f"  progress {applied}/{actual} avg_old_new_sim={avg_sim:.3f}"
                    )
            except Exception as exc:
                failed += 1
                self.stdout.write(
                    f"  [{idx}/{actual}] problem={problem.id} fail — {type(exc).__name__}: {exc}"
                )

        avg_sim = (
            sum(sim_distribution) / len(sim_distribution)
            if sim_distribution else float("nan")
        )
        self.stdout.write(
            f"[6.3S backfill DONE] tenant={tenant_id} "
            f"applied={applied} skipped={skipped} failed={failed} "
            f"avg_before_after_sim={avg_sim:.3f}"
        )
        if sim_distribution and avg_sim < 0.5:
            self.stderr.write(
                f"WARNING: avg before↔after cosine {avg_sim:.3f} < 0.5 — distribution shift 큼. "
                f"meta.image_embedding_pre_6_3s 로 rollback 가능."
            )
