"""Read-only manifest-based matchup segmentation audit.

This command is the broad counterpart to ``matchup_manual_gt_eval``. It reads a
tenant document manifest, routes each local original through the current
dispatcher with the document's own source_type, and writes inspectable per-doc
JSON plus an aggregate report. Documents with manual GT also receive IoU
metrics; documents without GT still receive structural quality metrics.
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.domains.matchup.management.commands.matchup_golden_eval import (
    _document_metrics,
)
from apps.domains.matchup.management.commands.matchup_manual_gt_eval import (
    _extract_ground_truth,
    _extract_predictions,
    _find_original_file,
    _json_default,
    evaluate_predictions_against_gt,
)


logger = logging.getLogger(__name__)

SCHEMA_VERSION = "matchup_manifest_segmentation_audit.v1"
DEFAULT_IOU_THRESHOLD = 0.50

STRUCTURAL_FAIL_FLAGS = frozenset({
    "eval_exception",
    "file_missing",
    "expected_positive_no_boxes",
    "non_question_expected_empty_has_boxes",
    "severe_under_expected_count",
})
STRUCTURAL_WARN_FLAGS = frozenset({
    "under_expected_count",
    "over_expected_count",
    "many_unnumbered_boxes",
    "manifest_gt_missed",
    "manifest_gt_precision_low",
})


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[6]


def _default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return (
        _workspace_root()
        / "_artifacts"
        / "sessions"
        / "matchup-manifest-segmentation-audit"
        / stamp
    )


def _safe_doc_filename(doc_id: int) -> str:
    return f"doc-{doc_id}.json"


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _select_documents(
    manifest: dict[str, Any],
    *,
    doc_ids: set[int],
    include_student_photo: bool,
    include_non_target: bool,
) -> list[dict[str, Any]]:
    documents = manifest.get("documents") or []
    if not isinstance(documents, list):
        raise CommandError("manifest.documents must be a list")

    selected: list[dict[str, Any]] = []
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        doc_id = _int_value(doc.get("id"), -1)
        if doc_id <= 0:
            continue
        if doc_ids and doc_id not in doc_ids:
            continue
        if not include_non_target and not doc.get("target_non_photo"):
            continue
        if (
            not include_student_photo
            and doc.get("meta_source_type") == "student_exam_photo"
        ):
            continue
        selected.append(doc)
    return selected


def _structural_flags(
    *,
    doc: dict[str, Any],
    metric: dict[str, Any],
    gt_metric: dict[str, Any] | None,
) -> list[str]:
    flags: list[str] = []
    expected = _int_value(doc.get("problem_rows") or doc.get("problem_count"))
    total_boxes = _int_value(metric.get("total_boxes"))
    paper_primary = str(doc.get("paper_primary") or "")
    non_question_expected_empty = paper_primary in {"answer_key", "explanation"}

    if expected > 0 and total_boxes == 0:
        flags.append("expected_positive_no_boxes")
    if non_question_expected_empty and total_boxes > 0:
        flags.append("non_question_expected_empty_has_boxes")
    if expected >= 5:
        ratio = total_boxes / expected if expected else 0.0
        if ratio < 0.75:
            flags.append("severe_under_expected_count")
        elif ratio < 0.90:
            flags.append("under_expected_count")
        if ratio > 1.60:
            flags.append("over_expected_count")

    numbered = _int_value(metric.get("numbered_box_count"))
    unnumbered = _int_value(metric.get("unnumbered_box_count"))
    if total_boxes >= 5 and numbered > 0 and unnumbered / max(total_boxes, 1) >= 0.30:
        flags.append("many_unnumbered_boxes")

    if gt_metric is not None:
        if _int_value(gt_metric.get("missed_count")) > 0:
            flags.append("manifest_gt_missed")
        if float(gt_metric.get("precision") or 0.0) < 0.60:
            flags.append("manifest_gt_precision_low")
    return flags


def _manifest_quality_grade(flags: list[str]) -> dict[str, Any]:
    fail = Counter(flag for flag in flags if flag in STRUCTURAL_FAIL_FLAGS)
    warn = Counter(flag for flag in flags if flag in STRUCTURAL_WARN_FLAGS)
    if fail:
        status = "fail"
    elif warn:
        status = "warn"
    else:
        status = "pass"
    return {
        "status": status,
        "fail_flags": dict(sorted(fail.items())),
        "warn_flags": dict(sorted(warn.items())),
    }


def _doc_summary(
    doc: dict[str, Any],
    *,
    metric: dict[str, Any],
    gt_metric: dict[str, Any] | None,
    structural_flags: list[str],
) -> dict[str, Any]:
    expected = _int_value(doc.get("problem_rows") or doc.get("problem_count"))
    total_boxes = _int_value(metric.get("total_boxes"))
    count_ratio = round(total_boxes / expected, 6) if expected else None
    return {
        "doc_id": _int_value(doc.get("id")),
        "title": doc.get("title") or "",
        "original_name": doc.get("original_name") or "",
        "source_type": doc.get("meta_source_type") or "",
        "paper_primary": doc.get("paper_primary") or "",
        "processing_quality": doc.get("processing_quality"),
        "segmentation_method_current": doc.get("segmentation_method"),
        "expected_problem_rows": expected,
        "manual_rows": _int_value(doc.get("manual_rows")),
        "has_ground_truth": bool(doc.get("ground_truth")),
        "predicted_boxes": total_boxes,
        "count_ratio": count_ratio,
        "page_count": metric.get("page_count"),
        "text_page_count": metric.get("text_page_count"),
        "skip_page_count": metric.get("skip_page_count"),
        "empty_page_count": metric.get("empty_page_count"),
        "paper_type_distribution": metric.get("paper_type_distribution") or {},
        "golden_quality_grade": metric.get("quality_grade") or {},
        "golden_quality_flags": metric.get("quality_flag_counts") or {},
        "manifest_structural_flags": structural_flags,
        "manifest_quality_grade": _manifest_quality_grade(structural_flags),
        "gt_metrics": {
            key: gt_metric.get(key)
            for key in (
                "status",
                "gt_count",
                "pred_count",
                "matched_count",
                "missed_count",
                "extra_count",
                "recall",
                "precision",
                "mean_iou",
            )
        } if gt_metric else None,
        "artifact_path": metric.get("artifact_path"),
    }


def _write_report(summary: dict[str, Any], report_path: Path) -> None:
    aggregate = summary["aggregate"]
    lines = [
        "# Matchup Manifest Segmentation Audit",
        "",
        f"- Ran at UTC: `{summary['ran_at_utc']}`",
        f"- Manifest: `{summary['manifest']}`",
        f"- Input dir: `{summary['input_dir']}`",
        "- Mode: read-only, DB write 0, R2 write 0",
        f"- Documents: `{aggregate['evaluated_docs']}/{aggregate['selected_docs']}` evaluated",
        f"- Files missing: `{aggregate['file_missing_docs']}`",
        f"- Pages: `{aggregate['page_count']}`",
        f"- Boxes: `{aggregate['total_boxes']}`",
        f"- Expected problem rows: `{aggregate['expected_problem_rows']}`",
        "",
        "## Manifest Quality",
        "",
    ]
    for status, count in sorted(aggregate["manifest_quality_status_counts"].items()):
        lines.append(f"- `{status}`: {count}")
    lines.extend(["", "## Structural Flags", ""])
    if aggregate["manifest_structural_flag_counts"]:
        for flag, count in sorted(aggregate["manifest_structural_flag_counts"].items()):
            lines.append(f"- `{flag}`: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Source/Paper Types", ""])
    for key, count in sorted(aggregate["source_paper_counts"].items()):
        lines.append(f"- `{key}`: {count}")

    gt = aggregate["ground_truth"]
    lines.extend([
        "",
        "## Manual GT Subset",
        "",
        f"- GT docs: `{gt['docs']}`",
        f"- GT boxes: `{gt['gt_count']}`",
        f"- Matched: `{gt['matched_count']}`",
        f"- Missed: `{gt['missed_count']}`",
        f"- Extra: `{gt['extra_count']}`",
        f"- Recall: `{gt['recall']}`",
        f"- Precision: `{gt['precision']}`",
        "",
        "## Documents",
        "",
    ])
    for doc in summary["documents"]:
        grade = (doc.get("manifest_quality_grade") or {}).get("status", "unknown")
        flags = ", ".join(doc.get("manifest_structural_flags") or []) or "none"
        gt_metrics = doc.get("gt_metrics") or {}
        gt_text = (
            f" gt_missed={gt_metrics.get('missed_count')} recall={gt_metrics.get('recall')}"
            if gt_metrics
            else ""
        )
        lines.append(
            f"- `{grade}` doc#{doc['doc_id']} `{doc['source_type']}/{doc['paper_primary']}` "
            f"expected={doc['expected_problem_rows']} pred={doc['predicted_boxes']} "
            f"ratio={doc['count_ratio']} flags=({flags}){gt_text}"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Command(BaseCommand):
    help = "Read-only manifest-based segmentation audit for tenant materials."

    def add_arguments(self, parser):
        parser.add_argument("--manifest", required=True, help="Tenant manifest JSON path.")
        parser.add_argument("--input-dir", required=True, help="Directory with doc-<id> originals.")
        parser.add_argument("--output", default="", help="Artifact output directory.")
        parser.add_argument("--doc-id", action="append", type=int, default=[], help="Limit to doc id. Repeatable.")
        parser.add_argument("--limit", type=int, default=None, help="Limit selected documents after filtering.")
        parser.add_argument("--iou-threshold", type=float, default=DEFAULT_IOU_THRESHOLD)
        parser.add_argument("--include-student-photo", action="store_true")
        parser.add_argument("--include-non-target", action="store_true")
        parser.add_argument("--overlay-limit-docs", type=int, default=0)
        parser.add_argument("--overlay-limit-pages", type=int, default=4)
        parser.add_argument("--no-overlays", action="store_true")

    def handle(self, *args, **options):
        from academy.adapters.ai.detection.segment_dispatcher import (
            cleanup_pdf_seg_tmp_dirs,
            segment_questions_multipage,
        )

        manifest_path = Path(options["manifest"])
        input_dir = Path(options["input_dir"])
        if not manifest_path.is_file():
            raise CommandError(f"manifest not found: {manifest_path}")
        if not input_dir.is_dir():
            raise CommandError(f"input-dir not found: {input_dir}")

        iou_threshold = float(options["iou_threshold"])
        if not (0.0 < iou_threshold <= 1.0):
            raise CommandError("--iou-threshold must be in (0, 1]")
        overlay_limit_docs = int(options["overlay_limit_docs"])
        overlay_limit_pages = int(options["overlay_limit_pages"])
        if overlay_limit_docs < 0 or overlay_limit_pages < 0:
            raise CommandError("--overlay-limit-* must be >= 0")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        selected = _select_documents(
            manifest,
            doc_ids=set(options.get("doc_id") or []),
            include_student_photo=bool(options["include_student_photo"]),
            include_non_target=bool(options["include_non_target"]),
        )
        if options.get("limit") is not None:
            limit = int(options["limit"])
            if limit <= 0:
                raise CommandError("--limit must be positive")
            selected = selected[:limit]
        if not selected:
            raise CommandError("no documents selected")

        out_dir = Path(options.get("output") or _default_output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        docs_dir = out_dir / "documents"
        docs_dir.mkdir(parents=True, exist_ok=True)
        overlay_root = None
        if (
            not options["no_overlays"]
            and overlay_limit_docs > 0
            and overlay_limit_pages > 0
        ):
            overlay_root = out_dir / "overlays"

        self.stdout.write(self.style.NOTICE(
            f"manifest segmentation audit: docs={len(selected)} input={input_dir} -> {out_dir}"
        ))
        self.stdout.write(self.style.WARNING("read-only: DB write 0, R2 write 0"))

        documents: list[dict[str, Any]] = []
        full_results: list[dict[str, Any]] = []
        overlay_docs_rendered = 0
        for idx, doc in enumerate(selected, start=1):
            doc_id = _int_value(doc.get("id"))
            source_type = str(doc.get("meta_source_type") or "other")
            source_path = _find_original_file(input_dir, doc_id)
            if source_path is None:
                structural_flags = ["file_missing"]
                metric = {
                    "artifact_path": "",
                    "total_boxes": 0,
                    "page_count": 0,
                    "text_page_count": 0,
                    "skip_page_count": 0,
                    "empty_page_count": 0,
                    "paper_type_distribution": {},
                    "quality_grade": {"status": "fail"},
                    "quality_flag_counts": {"file_missing": 1},
                }
                doc_summary = _doc_summary(
                    doc,
                    metric=metric,
                    gt_metric=None,
                    structural_flags=structural_flags,
                )
                documents.append(doc_summary)
                full_results.append({"doc": doc, "metric": metric, "gt_metric": None})
                self.stdout.write(f"[{idx}/{len(selected)}] doc#{doc_id} file_missing")
                continue

            raw_result: dict[str, Any] | None = None
            try:
                raw_result = segment_questions_multipage(
                    str(source_path),
                    source_type=source_type,
                )
                overlay_dir = None
                if overlay_root is not None and overlay_docs_rendered < overlay_limit_docs:
                    overlay_dir = overlay_root
                    overlay_docs_rendered += 1
                metric = _document_metrics(
                    source_path,
                    raw_result,
                    source_type=source_type,
                    overlay_dir=overlay_dir,
                    overlay_limit_pages=overlay_limit_pages,
                )
                gt_boxes = _extract_ground_truth(doc)
                gt_metric = None
                if gt_boxes:
                    gt_metric = evaluate_predictions_against_gt(
                        gt_boxes,
                        _extract_predictions(raw_result),
                        iou_threshold=iou_threshold,
                        min_recall=1.0,
                        min_precision=0.0,
                    )
                structural_flags = _structural_flags(
                    doc=doc,
                    metric=metric,
                    gt_metric=gt_metric,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("manifest segmentation audit failed for doc %s", doc_id)
                gt_metric = None
                structural_flags = ["eval_exception"]
                metric = {
                    "input": str(source_path),
                    "filename": source_path.name,
                    "source_type": source_type,
                    "ok": False,
                    "error": repr(exc),
                    "page_count": 0,
                    "total_boxes": 0,
                    "counted_boxes": 0,
                    "numbered_box_count": 0,
                    "unnumbered_box_count": 0,
                    "text_page_count": 0,
                    "skip_page_count": 0,
                    "empty_page_count": 0,
                    "paper_type_distribution": {},
                    "quality_grade": {"status": "fail"},
                    "quality_flag_counts": {"eval_exception": 1},
                    "pages": [],
                }
            finally:
                if raw_result:
                    cleanup_pdf_seg_tmp_dirs(list(raw_result.get("tmp_dirs") or []))

            doc_path = docs_dir / _safe_doc_filename(doc_id)
            metric["artifact_path"] = str(doc_path)
            doc_summary = _doc_summary(
                doc,
                metric=metric,
                gt_metric=gt_metric,
                structural_flags=structural_flags,
            )
            doc_path.write_text(
                json.dumps(
                    {
                        "manifest_document": doc,
                        "segmentation_metric": metric,
                        "gt_metric": gt_metric,
                        "document_summary": doc_summary,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=_json_default,
                ),
                encoding="utf-8",
            )
            documents.append(doc_summary)
            full_results.append({"doc": doc, "metric": metric, "gt_metric": gt_metric})
            grade = doc_summary["manifest_quality_grade"]["status"]
            self.stdout.write(
                f"[{idx}/{len(selected)}] doc#{doc_id} {grade} "
                f"expected={doc_summary['expected_problem_rows']} "
                f"pred={doc_summary['predicted_boxes']} flags={structural_flags}"
            )

        evaluated_docs = [
            item for item in full_results
            if "file_missing" not in (
                item.get("metric", {}).get("quality_flag_counts") or {}
            )
        ]
        source_paper_counts = Counter(
            f"{doc['source_type']}/{doc['paper_primary']}"
            for doc in documents
        )
        structural_flag_counts = Counter(
            flag
            for doc in documents
            for flag in doc.get("manifest_structural_flags", [])
        )
        status_counts = Counter(
            (doc.get("manifest_quality_grade") or {}).get("status", "unknown")
            for doc in documents
        )
        gt_docs = [doc for doc in documents if doc.get("gt_metrics")]
        gt_count = sum(_int_value((doc["gt_metrics"] or {}).get("gt_count")) for doc in gt_docs)
        gt_matched = sum(_int_value((doc["gt_metrics"] or {}).get("matched_count")) for doc in gt_docs)
        gt_missed = sum(_int_value((doc["gt_metrics"] or {}).get("missed_count")) for doc in gt_docs)
        gt_extra = sum(_int_value((doc["gt_metrics"] or {}).get("extra_count")) for doc in gt_docs)

        summary = {
            "schema_version": SCHEMA_VERSION,
            "ran_at_utc": datetime.now(timezone.utc).isoformat(),
            "manifest": str(manifest_path),
            "input_dir": str(input_dir),
            "output_dir": str(out_dir),
            "dry_run": True,
            "db_writes": 0,
            "r2_writes": 0,
            "aggregate": {
                "selected_docs": len(selected),
                "evaluated_docs": len(evaluated_docs),
                "file_missing_docs": sum(
                    1 for doc in documents
                    if "file_missing" in doc.get("manifest_structural_flags", [])
                ),
                "page_count": sum(_int_value(item["metric"].get("page_count")) for item in evaluated_docs),
                "total_boxes": sum(_int_value(item["metric"].get("total_boxes")) for item in evaluated_docs),
                "expected_problem_rows": sum(_int_value(doc.get("expected_problem_rows")) for doc in documents),
                "source_paper_counts": dict(sorted(source_paper_counts.items())),
                "manifest_structural_flag_counts": dict(sorted(structural_flag_counts.items())),
                "manifest_quality_status_counts": dict(sorted(status_counts.items())),
                "ground_truth": {
                    "docs": len(gt_docs),
                    "gt_count": gt_count,
                    "matched_count": gt_matched,
                    "missed_count": gt_missed,
                    "extra_count": gt_extra,
                    "recall": round(gt_matched / gt_count, 6) if gt_count else 0.0,
                    "precision": round(gt_matched / (gt_matched + gt_extra), 6)
                    if gt_matched + gt_extra else 0.0,
                },
            },
            "documents": documents,
        }

        summary_path = out_dir / "_summary.json"
        report_path = out_dir / "REPORT.md"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        _write_report(summary, report_path)
        self.stdout.write(self.style.SUCCESS(
            f"done: summary={summary_path} report={report_path}"
        ))
