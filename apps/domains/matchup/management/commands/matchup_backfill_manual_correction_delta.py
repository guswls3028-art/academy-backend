# PATH: apps/domains/matchup/management/commands/matchup_backfill_manual_correction_delta.py
"""Stage 6.5-backfill — 기존 manual=true MatchupProblem 을 ManualCorrectionDelta 로 소급 INSERT.

배경:
  Stage 6.5 hook (manually_crop_problem 안의 _record_manual_correction_delta)
  가 2026-05-08 이후 cut 만 audit log 로 기록. 그 이전 학원장이 만든 수만 건의
  manual cut 들은 ManualCorrectionDelta 0건. V12 학습 / paper_type cluster /
  cross-tenant fingerprint 의 데이터 베이스라인 비어있음.

목표:
  status=any 인 MatchupProblem 중 meta.manual=True 인 row → ManualCorrectionDelta
  소급 INSERT. correction_type='manual_create'. (problem_id, correction_type) 으로
  idempotent — 6.5 hook 이 이미 기록한 delta 와 중복 X.

원칙 (사용자 directive 준수):
- tenant_id 필수 (cross-tenant 영구 차단)
- dry-run default — 명시 --no-dry-run 시만 INSERT
- selected_problem_ids / hit_report / callback / segment_dispatcher 미접근
- R2 write / OCR/VLM / MatchupProblem 수정 0
- bbox_norm 또는 page_index 누락 problem skip + log
- document.tenant_id != problem.tenant_id mismatch skip + log
- failure isolation (한 problem 실패 시 다른 problem 계속)

Usage:
  python manage.py matchup_backfill_manual_correction_delta --tenant-id 2 --limit 100
  python manage.py matchup_backfill_manual_correction_delta --tenant-id 2 --no-dry-run --limit 500
"""
from __future__ import annotations

import logging
import time
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from apps.domains.matchup.models import (
    ManualCorrectionDelta, MatchupDocument, MatchupProblem,
)
from apps.domains.matchup.services import _record_manual_correction_delta


logger = logging.getLogger(__name__)


DEFAULT_LIMIT = 100
DEFAULT_SAMPLE = 10
CORRECTION_TYPE = "manual_create"


class Command(BaseCommand):
    help = (
        "Stage 6.5-backfill — 기존 manual=true MatchupProblem 을 "
        "ManualCorrectionDelta 로 소급 INSERT. tenant 필수. "
        "default dry-run (실 INSERT 0). --no-dry-run 명시 시만 INSERT."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-id", type=int, required=True,
            help="대상 tenant id (필수, cross-tenant 작업 영구 차단).",
        )
        parser.add_argument(
            "--limit", type=int, default=DEFAULT_LIMIT,
            help=f"batch size (default {DEFAULT_LIMIT}, max 5000).",
        )
        parser.add_argument(
            "--dry-run", dest="dry_run", action="store_true", default=True,
            help="대상 list + 결과 미리보기. INSERT 0 (default).",
        )
        parser.add_argument(
            "--no-dry-run", dest="dry_run", action="store_false",
            help="실 INSERT 활성화. dry-run 검토 후에만 사용.",
        )
        parser.add_argument(
            "--sample", type=int, default=DEFAULT_SAMPLE,
            help=f"dry-run 시 출력할 sample 개수 (default {DEFAULT_SAMPLE}).",
        )

    def handle(self, *args, **options):
        tenant_id: int = options["tenant_id"]
        limit: int = options["limit"]
        dry_run: bool = options["dry_run"]
        sample: int = options["sample"]

        if tenant_id <= 0:
            raise CommandError(f"invalid tenant_id={tenant_id}")
        if limit <= 0 or limit > 5000:
            raise CommandError(f"limit must be in (0, 5000]: got {limit}")
        if sample < 0:
            raise CommandError(f"sample must be >= 0: got {sample}")

        # 1) 대상 후보 — manual=true, tenant 안에서만
        qs = (
            MatchupProblem.objects
            .filter(tenant_id=tenant_id, meta__manual=True)
            .select_related("document")
            .order_by("-created_at")
        )
        total_manual = qs.count()

        # 이미 backfilled 된 problem id 집합 (idempotency)
        already_backfilled = set(
            ManualCorrectionDelta.objects
            .filter(tenant_id=tenant_id, correction_type=CORRECTION_TYPE)
            .exclude(problem_id__isnull=True)
            .values_list("problem_id", flat=True)
        )

        self.stdout.write(self.style.NOTICE(
            f"Stage 6.5-backfill — tenant={tenant_id} limit={limit} "
            f"dry_run={dry_run}"
        ))
        self.stdout.write(self.style.NOTICE(
            f"total manual=true MatchupProblem (tenant {tenant_id}): {total_manual}"
        ))
        self.stdout.write(self.style.NOTICE(
            f"already_backfilled (problem_id in delta with correction_type=manual_create): "
            f"{len(already_backfilled)}"
        ))

        # 2) 후보 분류 + skip 사유 분류
        candidates = []
        skip_already = 0
        skip_no_bbox = 0
        skip_merged = 0  # meta.merged=True + bbox 없음 — merge 결과는 manual_create 부적합
        skip_no_page = 0
        skip_tenant_mismatch = 0
        skip_no_document = 0
        # skip_merged 케이스 sample 보존 — 보고서 audit trail.
        merged_samples: list[dict] = []

        # iterator + limit — 메모리 안전
        iterated = 0
        for p in qs.iterator(chunk_size=200):
            iterated += 1
            if p.id in already_backfilled:
                skip_already += 1
                continue
            if not p.document_id:
                skip_no_document += 1
                continue
            # 3-way tenant consistency
            if p.document and p.document.tenant_id != p.tenant_id:
                skip_tenant_mismatch += 1
                continue
            if p.tenant_id != tenant_id:
                # query filter 가 이미 잡지만 방어
                skip_tenant_mismatch += 1
                continue
            meta = p.meta or {}
            bbox = meta.get("bbox_norm")
            bbox_invalid = (not isinstance(bbox, list) or len(bbox) < 4)
            if bbox_invalid:
                # meta.merged=True + bbox 부재 → merge 결과 (단일 bbox 없음).
                # manual_create backfill 대상 X. 별도 카테고리 + sample 보존.
                if meta.get("merged") is True:
                    skip_merged += 1
                    merged_samples.append({
                        "problem_id": p.id,
                        "doc_id": p.document_id,
                        "number": p.number,
                        "merged_from": meta.get("merged_from"),
                        "merged_numbers": meta.get("merged_numbers"),
                        "image_key": p.image_key,
                    })
                    continue
                skip_no_bbox += 1
                continue
            page_index = meta.get("page_index")
            if page_index is None:
                skip_no_page += 1
                continue
            try:
                bbox_tuple = (
                    float(bbox[0]), float(bbox[1]),
                    float(bbox[2]), float(bbox[3]),
                )
                page_index_int = int(page_index)
            except (TypeError, ValueError):
                skip_no_bbox += 1
                continue
            candidates.append((p, page_index_int, bbox_tuple))
            if len(candidates) >= limit:
                break

        self.stdout.write(self.style.NOTICE(
            f"iterated={iterated} candidates={len(candidates)} "
            f"skip_already={skip_already} skip_merged={skip_merged} "
            f"skip_no_bbox={skip_no_bbox} skip_no_page={skip_no_page} "
            f"skip_tenant_mismatch={skip_tenant_mismatch} "
            f"skip_no_document={skip_no_document}"
        ))
        # skip_merged sample 출력 — 추후 stage (correction_type='merge' 정책) 검토용.
        if merged_samples:
            self.stdout.write(self.style.NOTICE(
                f"--- skip_merged sample ({min(len(merged_samples), 10)}) ---"
            ))
            for s in merged_samples[:10]:
                self.stdout.write(
                    f"  problem#{s['problem_id']} doc#{s['doc_id']} "
                    f"num={s['number']} merged_from={s['merged_from']} "
                    f"merged_numbers={s['merged_numbers']} image_key={s['image_key']}"
                )

        if not candidates:
            self.stdout.write(self.style.SUCCESS("no candidates."))
            return

        # 3) sample 출력 (항상)
        self.stdout.write(self.style.NOTICE(f"--- sample (first {sample}) ---"))
        for p, page_index, bbox in candidates[:sample]:
            paper_type = ""
            if p.document and p.document.meta:
                summary = p.document.meta.get("paper_type_summary") or {}
                paper_type = str(summary.get("primary") or "")
            self.stdout.write(
                f"  problem#{p.id} doc#{p.document_id} num={p.number} "
                f"page={page_index} bbox={bbox} pt={paper_type!r} "
                f"created={p.created_at.isoformat()}"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"dry-run: 실 INSERT 0. --no-dry-run 명시 시 실제 처리."
            ))
            return

        # 4) 실 INSERT — 한 row씩 (failure isolation)
        succeeded = 0
        failed = 0
        durations = []
        engine_dist: Counter = Counter()
        paper_type_dist: Counter = Counter()
        for p, page_index, bbox_tuple in candidates:
            t0 = time.time()
            try:
                _record_manual_correction_delta(
                    p, p.document,
                    page_index=page_index,
                    bbox_norm=bbox_tuple,
                    is_recreate=False,
                    actor=None,  # 이전 데이터 — actor 모름
                )
                succeeded += 1
                # 분포 수집 — engine_at_action 은 대부분 manual_crop, paper_type 은 doc 의존
                paper_type = ""
                if p.document and p.document.meta:
                    summary = p.document.meta.get("paper_type_summary") or {}
                    paper_type = str(summary.get("primary") or "")
                paper_type_dist[paper_type or "<empty>"] += 1
                engine_dist["manual_crop"] += 1
            except Exception as exc:
                failed += 1
                logger.exception(
                    "MATCHUP_BACKFILL_MCD_FAILED | tenant=%s | problem=%s",
                    tenant_id, p.id,
                )
                self.stdout.write(self.style.ERROR(
                    f"  FAIL problem#{p.id}: {type(exc).__name__}: {exc}"
                ))
            durations.append(time.time() - t0)

        avg = sum(durations) / len(durations) if durations else 0.0
        self.stdout.write(self.style.SUCCESS(
            f"summary: succeeded={succeeded} failed={failed} "
            f"avg_ms={avg*1000:.1f}"
        ))
        if paper_type_dist:
            self.stdout.write(
                f"  paper_type_dist: {dict(paper_type_dist)}"
            )
