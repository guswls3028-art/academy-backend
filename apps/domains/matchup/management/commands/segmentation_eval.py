"""Stage 5.0 dry-run segmentation eval runner.

Tier 0 (Native PDF parser) prototype 으로 PDF 1개 또는 디렉토리 안 PDF 들을 분석.
운영 DB 어떤 변경도 X — 결과는 _artifacts JSON 으로만 저장.

원칙 (사용자 directive):
- 운영 MatchupProblem / selected_problem_ids / hit_report 미접근.
- manual=true MatchupProblem 미수정.
- ProblemSegmentationProposal 도 INSERT X (이번 prototype 단계).
- VLM 호출 X (Tier 0 only — 비용 0).

usage:
    python manage.py segmentation_eval \\
        --pdf <PATH-TO-PDF> \\
        --output _artifacts/sessions/matchup-rebuild-2026-05-05/stage5_eval/

    python manage.py segmentation_eval \\
        --pdf-dir <DIR> \\
        --output _artifacts/sessions/matchup-rebuild-2026-05-05/stage5_eval/

Stage 5.1 부터 28 doc 평가셋 추가 예정.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Stage 5.0 dry-run segmentation eval — Tier 0 Native PDF parser. DB write 0회."

    def add_arguments(self, parser):
        parser.add_argument(
            "--pdf",
            type=str,
            help="단일 PDF 파일 경로",
        )
        parser.add_argument(
            "--pdf-dir",
            type=str,
            help="PDF 들이 있는 디렉토리 — *.pdf 일괄 처리",
        )
        parser.add_argument(
            "--output",
            type=str,
            default="_artifacts/sessions/matchup-rebuild-2026-05-05/stage5_eval/",
            help="결과 JSON 저장 디렉토리. default _artifacts/sessions/matchup-rebuild-2026-05-05/stage5_eval/",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="(deprecated; default 항상 True) DB write 안 함",
        )

    def handle(self, *args, **options):
        from apps.domains.matchup.segmentation.tier0_native_pdf import analyze_pdf

        pdf = options.get("pdf")
        pdf_dir = options.get("pdf_dir")
        output = options.get("output")

        if not pdf and not pdf_dir:
            raise CommandError("--pdf 또는 --pdf-dir 중 하나 필요")
        if pdf and pdf_dir:
            raise CommandError("--pdf 와 --pdf-dir 동시 사용 불가")

        out_dir = Path(output)
        out_dir.mkdir(parents=True, exist_ok=True)

        targets: list[Path] = []
        if pdf:
            p = Path(pdf)
            if not p.is_file():
                raise CommandError(f"PDF 파일 없음: {pdf}")
            targets.append(p)
        else:
            d = Path(pdf_dir)
            if not d.is_dir():
                raise CommandError(f"디렉토리 없음: {pdf_dir}")
            targets = sorted(d.glob("*.pdf"))
            if not targets:
                raise CommandError(f"PDF 파일 없음 in {pdf_dir}")

        self.stdout.write(self.style.NOTICE(
            f"Stage 5.0 Tier 0 dry-run eval — {len(targets)} PDF(s) → {out_dir}"
        ))
        self.stdout.write(self.style.WARNING(
            "DB write 0회 (분석 결과는 artifact JSON 만)."
        ))

        summary: list[dict] = []
        for target in targets:
            self.stdout.write(f"\n  → analyzing {target.name}")
            try:
                result = analyze_pdf(str(target))
            except Exception as e:  # noqa: BLE001
                logger.exception("analyze_pdf failed: %s", target)
                self.stdout.write(self.style.ERROR(f"    fail: {e}"))
                summary.append({
                    "pdf": str(target),
                    "ok": False,
                    "error": repr(e),
                })
                continue

            # per-PDF JSON dump
            safe_name = target.stem.replace("/", "_").replace(" ", "_")
            out_file = out_dir / f"tier0_{safe_name}.json"
            out_file.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            # 요약
            n_pages = result["page_count"]
            n_anchors = sum(p["anchor_count"] for p in result["pages"])
            n_candidates = sum(len(p["bbox_candidates"]) for p in result["pages"])
            n_text_pages = sum(1 for p in result["pages"] if p["has_embedded_text"])
            roles = {}
            for p in result["pages"]:
                roles[p["role"]] = roles.get(p["role"], 0) + 1

            self.stdout.write(
                f"    pages={n_pages} text_pages={n_text_pages} anchors={n_anchors} candidates={n_candidates} roles={roles}"
            )
            summary.append({
                "pdf": str(target),
                "ok": True,
                "pages": n_pages,
                "text_pages": n_text_pages,
                "anchors": n_anchors,
                "bbox_candidates": n_candidates,
                "roles": roles,
                "out_file": str(out_file),
            })

        # 전체 요약
        summary_path = out_dir / f"_summary_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        summary_path.write_text(
            json.dumps({
                "stage": "5.0",
                "tier": 0,
                "engine": "native_pdf (PyMuPDF)",
                "ran_at_utc": datetime.now(timezone.utc).isoformat(),
                "dry_run": True,
                "db_writes": 0,
                "results": summary,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        ok_count = sum(1 for s in summary if s["ok"])
        self.stdout.write(self.style.SUCCESS(
            f"\n완료: {ok_count}/{len(summary)} 성공 / 요약: {summary_path}"
        ))
