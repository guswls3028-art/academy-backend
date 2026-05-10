# PATH: apps/domains/matchup/management/commands/matchup_export_v13_finetune_dataset.py
"""V13 학습 dataset export — fine-tune diff 신호 (V11 BOTTLENECK §7.1, 2026-05-10).

목적:
  AutoSegmentationSnapshot (자동 cut) + ManualCorrectionDelta (학원장 교정) JOIN
  으로 'manual diff' direct 신호 export. paradigm 한계 (V12=V11 D 등급) 돌파 path.

V12 export 와 차이:
  - V12 = manual cut 좌표만 (학원장 정답)
  - V13 = manual cut + AI 가 어디서 틀렸는지 (delta + IoU)
  - V13 학습 시 'manual_assist' loss term: predicted_bbox 가 corrected_bbox 와
    가까워지도록 + IoU 가 낮은 case 에 weight 강화

read-only:
  모든 ORM 쿼리 SELECT only. DB write 0, R2 write 0, OCR/VLM 호출 0.
  output JSONL 만 생성 (--no-dry-run 시).

흐름:
  1. ManualCorrectionDelta tenant filter + correction_type='manual_create' / 'bbox_adjust'
  2. snapshot_match: original_bbox / iou_with_ai 가 NULL 아닌 row 만 (= snapshot 매칭됨)
     - NULL 인 row = manual_only (V12 V13 둘 다 학습 가능, 신호 약함)
  3. doc / problem / paper_type / engine_at_action / corrected_bbox 결합
  4. JSONL row schema:
     {
       "tenant_id": 2,
       "doc_id": 615,
       "page_index": 16,
       "problem_number": 1,
       "paper_type": "clean_pdf_dual",
       "engine_at_action": "yolo_v11",
       "engine_version": "...",
       "manual_diff_present": true,
       "original_bbox": {x,y,w,h,page,norm},   # 자동 cut
       "corrected_bbox": {x,y,w,h,page,norm},  # 학원장 cut
       "iou_with_ai": 0.42,
       "diff_signal": {
         "dx": -0.012, "dy": +0.034,           # 좌표 차이
         "dw": +0.045, "dh": +0.156,
         "expanded": true                      # corrected 가 더 큰지 (over-crop 학습)
       },
       "correction_type": "manual_create",
       "image_key": "tenants/2/matchup/.../problems/1.png"
     }

사용:
  # dry-run default
  python manage.py matchup_export_v13_finetune_dataset --tenant-id 2 --output /tmp/v13.jsonl

  # actual
  python manage.py matchup_export_v13_finetune_dataset --tenant-id 2 --output /tmp/v13.jsonl --no-dry-run

  # snapshot matched only (manual diff 학습 신호)
  python manage.py matchup_export_v13_finetune_dataset --tenant-id 2 --output /tmp/v13.jsonl --snapshot-matched-only --no-dry-run
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Optional

from django.core.management.base import BaseCommand, CommandError


logger = logging.getLogger(__name__)


# correction_type 중 학습 신호로 적합한 것만.
TRAIN_CORRECTION_TYPES = frozenset({"manual_create", "bbox_adjust"})

EXCLUDED_PAPER_TYPES = frozenset({
    "non_question", "side_notes", "unknown",
    "explanation", "answer_key",
})


def _bbox_diff_signal(original: dict | None, corrected: dict | None) -> dict:
    """corrected_bbox - original_bbox 의 좌표 차이 + expanded flag."""
    if not isinstance(original, dict) or not isinstance(corrected, dict):
        return {}
    try:
        ox = float(original.get("x") or 0)
        oy = float(original.get("y") or 0)
        ow = float(original.get("w") or 0)
        oh = float(original.get("h") or 0)
        cx = float(corrected.get("x") or 0)
        cy = float(corrected.get("y") or 0)
        cw = float(corrected.get("w") or 0)
        ch = float(corrected.get("h") or 0)
    except (TypeError, ValueError):
        return {}
    expanded = cw > ow and ch > oh
    shrunk = cw < ow and ch < oh
    return {
        "dx": round(cx - ox, 6),
        "dy": round(cy - oy, 6),
        "dw": round(cw - ow, 6),
        "dh": round(ch - oh, 6),
        "expanded": expanded,
        "shrunk": shrunk,
    }


class Command(BaseCommand):
    help = "V13 fine-tune dataset export (manual diff + IoU)"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True, help="대상 tenant id")
        parser.add_argument("--output", type=str, required=True, help="출력 JSONL 경로")
        parser.add_argument("--no-dry-run", action="store_true",
                            help="--no-dry-run 시 파일 생성. 기본은 dry-run (summary 만)")
        parser.add_argument("--snapshot-matched-only", action="store_true",
                            help="snapshot 매칭된 row 만 export (manual diff 신호 강한 dataset)")

    def handle(self, *args, **options):
        from apps.domains.matchup.models import (
            ManualCorrectionDelta,
            MatchupDocument,
            MatchupProblem,
        )

        tenant_id: int = int(options["tenant_id"])
        output_path = Path(options["output"])
        dry_run = not options.get("no_dry_run", False)
        matched_only: bool = options.get("snapshot_matched_only", False)

        if tenant_id <= 0:
            raise CommandError("tenant-id 가 유효하지 않음")

        qs = (
            ManualCorrectionDelta.objects
            .filter(
                tenant_id=tenant_id,
                correction_type__in=list(TRAIN_CORRECTION_TYPES),
            )
            .select_related("document", "problem", "proposal")
            .order_by("created_at")
        )
        total_count = qs.count()
        self.stdout.write(f"[scan] tenant={tenant_id} ManualCorrectionDelta total: {total_count}")
        if total_count == 0:
            raise CommandError("export 대상 0건")

        # 통계 초기화
        stats = {
            "total": 0,
            "matched": 0,         # snapshot 매칭된 row (original_bbox not null)
            "manual_only": 0,     # snapshot 매칭 안 된 row
            "excluded_paper_type": 0,
            "no_corrected_bbox": 0,
            "by_paper_type": Counter(),
            "by_correction_type": Counter(),
            "by_engine_at_action": Counter(),
            "iou_buckets": Counter(),
            "diff_expanded_count": 0,
            "diff_shrunk_count": 0,
        }

        rows: list[dict] = []
        for d in qs.iterator():
            stats["total"] += 1

            paper_type = (d.paper_type_at_action or "").strip()
            if paper_type in EXCLUDED_PAPER_TYPES:
                stats["excluded_paper_type"] += 1
                continue

            corrected_bbox = d.corrected_bbox if isinstance(d.corrected_bbox, dict) else None
            if not corrected_bbox:
                stats["no_corrected_bbox"] += 1
                continue

            original_bbox = d.original_bbox if isinstance(d.original_bbox, dict) else None
            iou = d.iou_with_ai
            matched = original_bbox is not None and iou is not None
            if matched:
                stats["matched"] += 1
                # IoU bucket
                if iou < 0.3:
                    stats["iou_buckets"]["0.0-0.3"] += 1
                elif iou < 0.5:
                    stats["iou_buckets"]["0.3-0.5"] += 1
                elif iou < 0.7:
                    stats["iou_buckets"]["0.5-0.7"] += 1
                elif iou < 0.9:
                    stats["iou_buckets"]["0.7-0.9"] += 1
                else:
                    stats["iou_buckets"]["0.9-1.0"] += 1
            else:
                stats["manual_only"] += 1
                if matched_only:
                    continue  # --snapshot-matched-only 시 skip

            stats["by_paper_type"][paper_type or "(empty)"] += 1
            stats["by_correction_type"][d.correction_type] += 1
            stats["by_engine_at_action"][d.engine_at_action or "(empty)"] += 1

            diff_signal = _bbox_diff_signal(original_bbox, corrected_bbox)
            if diff_signal.get("expanded"):
                stats["diff_expanded_count"] += 1
            if diff_signal.get("shrunk"):
                stats["diff_shrunk_count"] += 1

            row = {
                "tenant_id": tenant_id,
                "doc_id": d.document_id,
                "problem_id": d.problem_id,
                "page_index": (corrected_bbox or {}).get("page"),
                "problem_number": getattr(d.problem, "number", None),
                "paper_type": paper_type,
                "engine_at_action": d.engine_at_action or "",
                "manual_diff_present": matched,
                "original_bbox": original_bbox,
                "corrected_bbox": corrected_bbox,
                "iou_with_ai": iou,
                "diff_signal": diff_signal,
                "correction_type": d.correction_type,
                "image_key": getattr(d.problem, "image_key", "") or "",
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            rows.append(row)

        # 통계 출력
        self.stdout.write("=== V13 fine-tune dataset summary ===")
        self.stdout.write(f"  total scanned: {stats['total']}")
        self.stdout.write(f"  matched (snapshot diff present): {stats['matched']} "
                          f"({(stats['matched'] / max(stats['total'], 1)) * 100:.1f}%)")
        self.stdout.write(f"  manual_only (no snapshot match): {stats['manual_only']}")
        self.stdout.write(f"  excluded paper_type: {stats['excluded_paper_type']}")
        self.stdout.write(f"  no corrected_bbox: {stats['no_corrected_bbox']}")
        self.stdout.write(f"  rows to export: {len(rows)}")
        self.stdout.write(f"  paper_type dist: {dict(stats['by_paper_type'])}")
        self.stdout.write(f"  correction_type dist: {dict(stats['by_correction_type'])}")
        self.stdout.write(f"  engine_at_action dist: {dict(stats['by_engine_at_action'])}")
        self.stdout.write(f"  iou bucket dist: {dict(stats['iou_buckets'])}")
        self.stdout.write(f"  diff expanded: {stats['diff_expanded_count']}, "
                          f"shrunk: {stats['diff_shrunk_count']}")

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "[dry-run] file 생성 안 함. --no-dry-run 시 export."
            ))
            return

        # actual write
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        self.stdout.write(self.style.SUCCESS(
            f"[exported] rows={len(rows)} → {output_path}"
        ))
