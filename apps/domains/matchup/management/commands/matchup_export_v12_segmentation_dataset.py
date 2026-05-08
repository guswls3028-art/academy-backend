# PATH: apps/domains/matchup/management/commands/matchup_export_v12_segmentation_dataset.py
"""Stage P1 — V12 자동분리 학습/평가 데이터셋 read-only export.

목적: ManualCorrectionDelta 4,018 + LayoutFingerprint 222 + 부속 doc/problem
메타를 결합해 V12 학습용 JSONL 파일 생성. 학습 자체는 별도 stage. 본 command
는 read-only — DB write 0, R2 write 0, OCR/VLM 호출 0.

원칙 (사용자 directive):
- read-only — 모든 ORM 쿼리는 SELECT 만, 어떤 .save() / .create() / .update() 도 X
- tenant_id 필수 (cross-tenant 영구 차단)
- doc-level split (data leakage 방지)
- paper_type stratified split
- 학생답안지 / 스캔본은 eval subset 분리 (V12 평가 전용)
- skip_merged 자동 제외 (correction_type 으로 필터)
- indexable=False doc 제외 (V12 풀 진입 부적합)
- non_question / side_notes / unknown / explanation / answer_key paper_type 제외

Usage:
  python manage.py matchup_export_v12_segmentation_dataset \
      --tenant-id 2 --output /tmp/v12.jsonl

  # dry-run default — 파일 생성 안 함, summary 만 출력
  python manage.py matchup_export_v12_segmentation_dataset \
      --tenant-id 2 --output /tmp/v12.jsonl --dry-run

  # actual export (DB write 없음, 파일만 생성)
  python manage.py matchup_export_v12_segmentation_dataset \
      --tenant-id 2 --output /tmp/v12.jsonl --no-dry-run
"""
from __future__ import annotations

import json
import logging
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from django.core.management.base import BaseCommand, CommandError


logger = logging.getLogger(__name__)


CORRECTION_TYPE = "manual_create"
EXCLUDED_PAPER_TYPES = frozenset({
    "non_question", "side_notes", "unknown",
    "explanation", "answer_key",
})
EVAL_PHOTO_TYPES = frozenset({"student_answer_photo"})
EVAL_SCAN_TYPES = frozenset({"scan_single", "scan_dual"})

# clean PDF 와 동급 — train/val/test 으로 분배.
TRAIN_TYPES = frozenset({"clean_pdf_dual", "clean_pdf_single", "quadrant"})

# Split 비율 (TRAIN_TYPES 만 적용 — eval 은 100% subset).
SPLIT_RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}


class Command(BaseCommand):
    help = (
        "V12 자동분리 학습/평가 JSONL dataset export. read-only — "
        "DB write 0, R2 write 0. tenant 필수. dry-run default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-id", type=int, required=True,
            help="대상 tenant id (필수, cross-tenant 작업 영구 차단).",
        )
        parser.add_argument(
            "--output", type=str, required=True,
            help="JSONL 출력 경로 (--no-dry-run 시 파일 작성).",
        )
        parser.add_argument(
            "--dry-run", dest="dry_run", action="store_true", default=True,
            help="파일 생성 X, summary 만 출력 (default).",
        )
        parser.add_argument(
            "--no-dry-run", dest="dry_run", action="store_false",
            help="실 파일 작성. DB write 는 여전히 X.",
        )
        parser.add_argument(
            "--seed", type=int, default=None,
            help="split 결정 seed (default tenant_id 기반 deterministic).",
        )
        parser.add_argument(
            "--sample", type=int, default=10,
            help=f"summary 출력 sample row 수 (default 10).",
        )

    def handle(self, *args, **options):
        from apps.domains.matchup.models import (
            ManualCorrectionDelta, MatchupDocument, MatchupProblem,
            LayoutFingerprint,
        )

        tenant_id: int = options["tenant_id"]
        output: str = options["output"]
        dry_run: bool = options["dry_run"]
        seed = options.get("seed")
        sample: int = options["sample"]

        if tenant_id <= 0:
            raise CommandError(f"invalid tenant_id={tenant_id}")
        if not output:
            raise CommandError("--output is required")
        if sample < 0:
            raise CommandError(f"sample must be >= 0: got {sample}")
        if seed is None:
            seed = 42 + tenant_id

        # 1) MCD 후보 — manual_create 만, tenant 격리
        mcd_qs = (
            ManualCorrectionDelta.objects
            .filter(tenant_id=tenant_id, correction_type=CORRECTION_TYPE)
            .select_related("document", "problem")
            .order_by("created_at")
        )
        mcd_total = mcd_qs.count()

        # 2) 부속 메타 사전 fetch — read-only
        # LayoutFingerprint version=1
        lf_by_doc = {
            lf.document_id: lf
            for lf in LayoutFingerprint.objects.filter(
                tenant_id=tenant_id, fingerprint_version=1,
            )
        }

        # 3) row 후보 분류
        rows: list[dict] = []
        skip_no_bbox = 0
        skip_no_page = 0
        skip_no_doc = 0
        skip_no_problem = 0
        skip_indexable_false = 0
        skip_paper_type = 0
        skip_paper_type_dist: Counter = Counter()
        included_paper_type_dist: Counter = Counter()

        for d in mcd_qs.iterator(chunk_size=500):
            if not d.document_id or not d.document:
                skip_no_doc += 1
                continue
            if not d.problem_id:
                skip_no_problem += 1
                continue
            doc = d.document
            doc_meta = doc.meta or {}
            if doc_meta.get("indexable") is False:
                skip_indexable_false += 1
                continue
            cb = d.corrected_bbox or {}
            if not isinstance(cb, dict):
                skip_no_bbox += 1
                continue
            if not all(k in cb for k in ("x", "y", "w", "h")):
                skip_no_bbox += 1
                continue
            page_index = cb.get("page")
            if page_index is None:
                page_index = (d.problem.meta or {}).get("page_index") if d.problem else None
            if page_index is None:
                skip_no_page += 1
                continue

            # paper_type 결정 — paper_type_at_action 우선, fallback doc.meta
            paper_type = d.paper_type_at_action or ""
            if not paper_type:
                summary = doc_meta.get("paper_type_summary") or {}
                paper_type = str(summary.get("primary") or "")
            if paper_type in EXCLUDED_PAPER_TYPES or not paper_type:
                skip_paper_type += 1
                skip_paper_type_dist[paper_type or "<empty>"] += 1
                continue

            # split 결정 — eval subset 분리
            if paper_type in EVAL_PHOTO_TYPES:
                split = "eval_photo"
            elif paper_type in EVAL_SCAN_TYPES:
                split = "eval_scan"
            elif paper_type in TRAIN_TYPES:
                split = None  # doc-level deterministic split — 후속 로직
            else:
                # 정의 안 된 paper_type — eval 로 보수
                split = "eval_photo"

            lf = lf_by_doc.get(doc.id)

            row = {
                "tenant_id": d.tenant_id,
                "document_id": doc.id,
                "problem_id": d.problem_id,
                "correction_delta_id": d.id,
                "page_index": int(page_index),
                "problem_number": d.problem.number if d.problem else None,
                "corrected_bbox": dict(cb),
                "bbox_format": "normalized_dict" if cb.get("norm") else "raw_dict",
                "paper_type": paper_type,
                "processing_quality": doc_meta.get("processing_quality"),
                "indexable": doc_meta.get("indexable"),
                "layout_fingerprint_id": lf.id if lf else None,
                "page_size": (lf.page_size if lf else None) or doc_meta.get("page_size"),
                "column_count": lf.column_count if lf else None,
                "image_key": d.problem.image_key if d.problem else None,
                "created_at": d.created_at.isoformat(),
                "split": split,  # None == TRAIN_TYPES (이후 단계에서 결정)
                "warnings": [],
            }
            rows.append(row)
            included_paper_type_dist[paper_type] += 1

        # 4) doc-level split 적용 (TRAIN_TYPES 만)
        # paper_type 별 stratified — 같은 paper_type 내에서 doc id 들을 70/15/15 분배
        rng = random.Random(seed)
        doc_to_paper: dict[int, str] = {}
        for r in rows:
            if r["split"] is not None:
                continue
            doc_to_paper.setdefault(r["document_id"], r["paper_type"])

        # paper_type → doc_id list (sorted for determinism)
        paper_to_docs: dict[str, list[int]] = defaultdict(list)
        for did, pt in doc_to_paper.items():
            paper_to_docs[pt].append(did)
        for pt in paper_to_docs:
            paper_to_docs[pt].sort()
            rng.shuffle(paper_to_docs[pt])

        # 각 paper_type 별 doc_id → split 매핑
        doc_split: dict[int, str] = {}
        split_counts_doc: Counter = Counter()
        for pt, dids in paper_to_docs.items():
            n = len(dids)
            n_train = max(1, int(n * SPLIT_RATIOS["train"]))
            n_val = max(0, int(n * SPLIT_RATIOS["val"]))
            # 나머지 = test
            for i, did in enumerate(dids):
                if i < n_train:
                    s = "train"
                elif i < n_train + n_val:
                    s = "val"
                else:
                    s = "test"
                doc_split[did] = s
                split_counts_doc[(pt, s)] += 1

        # row 에 split 할당
        for r in rows:
            if r["split"] is None:
                r["split"] = doc_split.get(r["document_id"], "train")

        # 5) split 통계 + leakage 체크
        split_counts: Counter = Counter()
        bbox_format_counts: Counter = Counter()
        leakage_check: dict[int, set[str]] = defaultdict(set)
        for r in rows:
            split_counts[r["split"]] += 1
            bbox_format_counts[r["bbox_format"]] += 1
            leakage_check[r["document_id"]].add(r["split"])
        leak_docs = [did for did, splits in leakage_check.items() if len(splits) > 1]

        # 6) summary 출력
        self.stdout.write(self.style.NOTICE(
            f"V12 dataset export — tenant={tenant_id} dry_run={dry_run}"
        ))
        self.stdout.write(self.style.NOTICE(
            f"MCD total ({CORRECTION_TYPE}): {mcd_total}"
        ))
        self.stdout.write(self.style.NOTICE(
            f"included rows: {len(rows)}"
        ))
        self.stdout.write(self.style.NOTICE(
            f"  skip_no_doc={skip_no_doc} skip_no_problem={skip_no_problem} "
            f"skip_no_bbox={skip_no_bbox} skip_no_page={skip_no_page} "
            f"skip_indexable_false={skip_indexable_false} "
            f"skip_paper_type={skip_paper_type}"
        ))
        if skip_paper_type_dist:
            self.stdout.write(
                f"  skip_paper_type_dist: {dict(skip_paper_type_dist)}"
            )
        self.stdout.write(self.style.NOTICE(
            f"included paper_type_dist: {dict(included_paper_type_dist)}"
        ))
        self.stdout.write(self.style.NOTICE(
            f"split_dist (rows): {dict(split_counts)}"
        ))
        self.stdout.write(self.style.NOTICE(
            f"bbox_format_dist: {dict(bbox_format_counts)}"
        ))
        if leak_docs:
            self.stdout.write(self.style.ERROR(
                f"❌ doc-level LEAKAGE detected on {len(leak_docs)} doc(s): "
                f"{leak_docs[:5]}..."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"✅ doc-level split leakage 0 (총 {len(leakage_check)} doc 중)"
            ))

        # 7) sample 출력
        if sample > 0 and rows:
            self.stdout.write(self.style.NOTICE(
                f"--- sample (first {min(sample, len(rows))}) ---"
            ))
            for r in rows[:sample]:
                short = {
                    "doc_id": r["document_id"], "problem_id": r["problem_id"],
                    "page": r["page_index"], "num": r["problem_number"],
                    "pt": r["paper_type"], "split": r["split"],
                    "bbox": r["corrected_bbox"],
                }
                self.stdout.write(f"  {json.dumps(short, ensure_ascii=False)}")

        # 8) actual write (read-only on DB; file 작성만)
        if dry_run:
            self.stdout.write(self.style.WARNING(
                "dry-run: 파일 생성 X. --no-dry-run 명시 시 파일 작성."
            ))
            return

        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str))
                f.write("\n")
        size = out_path.stat().st_size
        self.stdout.write(self.style.SUCCESS(
            f"✅ wrote {len(rows)} rows to {out_path} ({size} bytes)"
        ))
