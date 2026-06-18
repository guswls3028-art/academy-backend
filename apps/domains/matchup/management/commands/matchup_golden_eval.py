"""Read-only matchup golden-set evaluator.

This command runs the current segmentation dispatcher against local fixture
files and writes inspectable artifacts: per-document JSON, an aggregate JSON
summary, a Markdown report, and optional page overlays. It does not touch
MatchupDocument, MatchupProblem, selected_problem_ids, hit reports, R2, or any
other persistent product state.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.domains.matchup.source_types import (
    SOURCE_TYPES,
    is_indexable,
    normalize_source_type,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "matchup_golden_eval.v1"
SUPPORTED_SUFFIXES = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
})
NON_QUESTION_PAPER_TYPES = frozenset({
    "non_question", "side_notes", "explanation", "answer_key", "cover", "index",
})
LARGE_BOX_RATIO = 0.70
SMALL_BOX_RATIO = 0.01
FAIL_FLAGS = frozenset({
    "eval_exception",
    "page_like_box",
    "non_question_has_boxes",
    "skip_page_has_boxes",
    "no_boxes_non_skip",
    "number_length_mismatch",
    "duplicate_numbers",
})
WARN_FLAGS = frozenset({
    "all_boxes_unnumbered",
    "mixed_numbering",
    "tiny_box_spike",
    "image_size_missing",
})


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[6]


def _default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return (
        _workspace_root()
        / "_artifacts"
        / "sessions"
        / "matchup-golden-eval"
        / stamp
    )


def _safe_slug(value: str, *, max_len: int = 80) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    if not normalized:
        normalized = "item"
    normalized = normalized[:max_len].strip("-._") or "item"
    return f"{normalized}-{digest}"


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _image_size_for_page(page: dict[str, Any]) -> tuple[int, int] | None:
    explicit = page.get("image_size")
    if (
        isinstance(explicit, (list, tuple))
        and len(explicit) == 2
        and int(explicit[0]) > 0
        and int(explicit[1]) > 0
    ):
        return int(explicit[0]), int(explicit[1])

    image_path = page.get("image_path")
    if not image_path:
        return None
    try:
        from PIL import Image

        with Image.open(str(image_path)) as img:
            return int(img.width), int(img.height)
    except Exception as exc:  # noqa: BLE001
        logger.warning("golden eval could not read image size: %s", exc)
        return None


def _box_area_ratio(
    box: tuple[int, int, int, int] | list[int],
    image_size: tuple[int, int] | None,
) -> float | None:
    if not image_size:
        return None
    width, height = image_size
    page_area = float(width * height)
    if page_area <= 0:
        return None
    try:
        _, _, w, h = box
        return max(0.0, float(w) * float(h)) / page_area
    except Exception:
        return None


def _coerce_boxes(raw_boxes: Any) -> list[tuple[int, int, int, int]]:
    boxes: list[tuple[int, int, int, int]] = []
    if not isinstance(raw_boxes, list):
        return boxes
    for raw in raw_boxes:
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            continue
        try:
            x, y, w, h = raw
            boxes.append((int(x), int(y), int(w), int(h)))
        except (TypeError, ValueError):
            continue
    return boxes


def _page_metrics(
    page: dict[str, Any],
    *,
    overlay_path: str | None = None,
) -> dict[str, Any]:
    boxes = _coerce_boxes(page.get("boxes") or [])
    numbers = page.get("numbers") or []
    if not isinstance(numbers, list):
        numbers = []
    image_size = _image_size_for_page(page)

    ratios: list[float] = []
    large_box_count = 0
    small_box_count = 0
    for box in boxes:
        ratio = _box_area_ratio(box, image_size)
        if ratio is None:
            continue
        ratios.append(round(ratio, 5))
        if ratio >= LARGE_BOX_RATIO:
            large_box_count += 1
        if ratio <= SMALL_BOX_RATIO:
            small_box_count += 1

    numbered = [n for n in numbers if isinstance(n, int)]
    unique_numbered = sorted(set(numbered))
    metric = {
        "page_index": int(page.get("page_index") or 0),
        "paper_type": str(page.get("paper_type") or "unknown"),
        "has_embedded_text": bool(page.get("has_embedded_text")),
        "is_skip_page": bool(page.get("is_skip_page")),
        "image_path": str(page.get("image_path") or ""),
        "image_size": list(image_size) if image_size else None,
        "box_count": len(boxes),
        "number_count": len(numbers),
        "numbered_count": len(numbered),
        "unnumbered_count": max(0, len(boxes) - len(numbered)),
        "unique_numbers": unique_numbered,
        "duplicate_numbers": sorted(
            n for n, count in Counter(numbered).items() if count > 1
        ),
        "large_box_count": large_box_count,
        "small_box_count": small_box_count,
        "area_ratios": ratios,
        "overlay_path": overlay_path,
    }
    metric["quality_flags"] = _quality_flags(metric)
    return metric


def _quality_flags(page_metric: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    paper_type = str(page_metric.get("paper_type") or "unknown")
    box_count = int(page_metric.get("box_count") or 0)
    number_count = int(page_metric.get("number_count") or 0)
    numbered_count = int(page_metric.get("numbered_count") or 0)
    unnumbered_count = int(page_metric.get("unnumbered_count") or 0)

    if page_metric.get("image_size") is None:
        flags.append("image_size_missing")
    if number_count != box_count:
        flags.append("number_length_mismatch")
    if box_count == 0 and not page_metric.get("is_skip_page"):
        flags.append("no_boxes_non_skip")
    if page_metric.get("is_skip_page") and box_count > 0:
        flags.append("skip_page_has_boxes")
    if paper_type in NON_QUESTION_PAPER_TYPES and box_count > 0:
        flags.append("non_question_has_boxes")
    if int(page_metric.get("large_box_count") or 0) > 0:
        flags.append("page_like_box")
    if box_count > 0 and numbered_count == 0:
        flags.append("all_boxes_unnumbered")
    elif numbered_count > 0 and unnumbered_count > 0:
        flags.append("mixed_numbering")
    if page_metric.get("duplicate_numbers"):
        flags.append("duplicate_numbers")
    if (
        int(page_metric.get("small_box_count") or 0) >= max(3, box_count // 2)
        and box_count >= 4
    ):
        flags.append("tiny_box_spike")
    return flags


def _quality_grade(
    flag_counts: dict[str, int],
    *,
    ok: bool = True,
    skipped_for_indexing: bool = False,
) -> dict[str, Any]:
    if skipped_for_indexing:
        return {
            "status": "skip",
            "blocking_flags": {},
            "warning_flags": {},
        }
    if not ok:
        return {
            "status": "fail",
            "blocking_flags": {"eval_exception": 1},
            "warning_flags": {},
        }

    blocking = {
        flag: count
        for flag, count in flag_counts.items()
        if flag in FAIL_FLAGS and int(count) > 0
    }
    warnings = {
        flag: count
        for flag, count in flag_counts.items()
        if flag in WARN_FLAGS and int(count) > 0
    }
    if blocking:
        status = "fail"
    elif warnings:
        status = "warn"
    else:
        status = "pass"
    return {
        "status": status,
        "blocking_flags": dict(sorted(blocking.items())),
        "warning_flags": dict(sorted(warnings.items())),
    }


def _draw_overlay(page: dict[str, Any], output_path: Path) -> str | None:
    image_path = page.get("image_path")
    if not image_path:
        return "missing image_path"
    boxes = _coerce_boxes(page.get("boxes") or [])
    numbers = page.get("numbers") or []
    if not isinstance(numbers, list):
        numbers = []
    try:
        from PIL import Image, ImageDraw, ImageFont

        with Image.open(str(image_path)) as source:
            img = source.convert("RGB")
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        image_size = (img.width, img.height)
        line_width = max(2, min(img.width, img.height) // 350)

        for idx, (x, y, w, h) in enumerate(boxes):
            ratio = _box_area_ratio((x, y, w, h), image_size)
            color = (30, 160, 70)
            if ratio is not None and ratio >= LARGE_BOX_RATIO:
                color = (220, 45, 45)
            elif ratio is not None and ratio <= SMALL_BOX_RATIO:
                color = (230, 150, 35)
            draw.rectangle(
                (x, y, x + max(w, 1), y + max(h, 1)),
                outline=color,
                width=line_width,
            )
            number = numbers[idx] if idx < len(numbers) else None
            label = f"Q{number}" if isinstance(number, int) else f"?{idx + 1}"
            text_origin = (max(0, x), max(0, y - 14))
            draw.rectangle(
                (
                    text_origin[0],
                    text_origin[1],
                    text_origin[0] + 42,
                    text_origin[1] + 13,
                ),
                fill=color,
            )
            draw.text(text_origin, label, fill=(255, 255, 255), font=font)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path)
        return None
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def _document_metrics(
    target: Path,
    result: dict[str, Any],
    *,
    source_type: str,
    overlay_dir: Path | None,
    overlay_limit_pages: int,
) -> dict[str, Any]:
    pages = result.get("pages") or []
    if not isinstance(pages, list):
        pages = []

    page_metrics: list[dict[str, Any]] = []
    slug = _safe_slug(target.stem)
    overlay_errors: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        overlay_path: str | None = None
        if overlay_dir is not None and index < overlay_limit_pages:
            out_path = overlay_dir / slug / f"page_{index:03d}.png"
            error = _draw_overlay(page, out_path)
            if error is None:
                overlay_path = str(out_path)
            else:
                overlay_errors.append({
                    "page_index": int(page.get("page_index") or index),
                    "error": error,
                })
        page_metrics.append(_page_metrics(page, overlay_path=overlay_path))

    paper_types = Counter(p["paper_type"] for p in page_metrics)
    flag_counts = Counter(
        flag
        for page in page_metrics
        for flag in page.get("quality_flags", [])
    )
    total_boxes = sum(int(p["box_count"]) for p in page_metrics)
    numbered_count = sum(int(p["numbered_count"]) for p in page_metrics)
    unnumbered_count = sum(int(p["unnumbered_count"]) for p in page_metrics)
    skip_page_count = sum(1 for p in page_metrics if p["is_skip_page"])
    text_page_count = sum(1 for p in page_metrics if p["has_embedded_text"])
    empty_page_count = sum(1 for p in page_metrics if int(p["box_count"]) == 0)

    flag_counts_dict = dict(sorted(flag_counts.items()))
    doc = {
        "input": str(target),
        "filename": target.name,
        "source_type": source_type,
        "ok": True,
        "is_pdf": bool(result.get("is_pdf")),
        "page_count": len(page_metrics),
        "total_boxes": int(result.get("total_boxes") or total_boxes),
        "counted_boxes": total_boxes,
        "numbered_box_count": numbered_count,
        "unnumbered_box_count": unnumbered_count,
        "text_page_count": text_page_count,
        "skip_page_count": skip_page_count,
        "empty_page_count": empty_page_count,
        "paper_type_distribution": dict(sorted(paper_types.items())),
        "quality_flag_counts": flag_counts_dict,
        "overlay_errors": overlay_errors,
        "pages": page_metrics,
    }
    doc["quality_grade"] = _quality_grade(flag_counts_dict)
    return doc


def _skipped_document_metrics(target: Path, *, source_type: str) -> dict[str, Any]:
    doc = {
        "input": str(target),
        "filename": target.name,
        "source_type": source_type,
        "ok": True,
        "skipped_for_indexing": True,
        "skip_reason": "source_type_not_indexable",
        "is_pdf": target.suffix.lower() == ".pdf",
        "page_count": 0,
        "total_boxes": 0,
        "counted_boxes": 0,
        "numbered_box_count": 0,
        "unnumbered_box_count": 0,
        "text_page_count": 0,
        "skip_page_count": 0,
        "empty_page_count": 0,
        "paper_type_distribution": {source_type: 1},
        "quality_flag_counts": {},
        "overlay_errors": [],
        "pages": [],
    }
    doc["quality_grade"] = _quality_grade({}, skipped_for_indexing=True)
    return doc


def _collect_targets(files: list[str], input_dir: str | None) -> list[Path]:
    targets: list[Path] = []
    for raw in files:
        path = Path(raw)
        if not path.is_file():
            raise CommandError(f"file not found: {raw}")
        targets.append(path)

    if input_dir:
        root = Path(input_dir)
        if not root.is_dir():
            raise CommandError(f"directory not found: {input_dir}")
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                targets.append(path)

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in targets:
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _write_report(summary: dict[str, Any], report_path: Path) -> None:
    aggregate = summary["aggregate"]
    lines = [
        "# Matchup Golden Eval",
        "",
        f"- Ran at UTC: `{summary['ran_at_utc']}`",
        f"- Source type: `{summary['source_type']}`",
        "- Mode: read-only, DB write 0",
        f"- Files: `{aggregate['ok_files']}/{aggregate['total_files']}` ok",
        f"- Pages: `{aggregate['page_count']}`",
        f"- Boxes: `{aggregate['total_boxes']}`",
        "",
        "## Quality Flags",
        "",
    ]
    if aggregate["quality_flag_counts"]:
        for flag, count in sorted(aggregate["quality_flag_counts"].items()):
            lines.append(f"- `{flag}`: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Files", ""])

    for doc in summary["results"]:
        grade = doc.get("quality_grade") or {}
        status = grade.get("status") or ("ok" if doc.get("ok") else "fail")
        flags = doc.get("quality_flag_counts") or {}
        flag_text = ", ".join(f"{k}={v}" for k, v in sorted(flags.items())) or "none"
        paper_types = doc.get("paper_type_distribution") or {}
        paper_text = ", ".join(
            f"{k}={v}" for k, v in sorted(paper_types.items())
        ) or "none"
        lines.append(
            f"- `{status}` `{doc.get('filename')}`: "
            f"pages={doc.get('page_count', 0)} boxes={doc.get('total_boxes', 0)} "
            f"text_pages={doc.get('text_page_count', 0)} "
            f"paper_types=({paper_text}) flags=({flag_text})"
        )
        if doc.get("error"):
            lines.append(f"  error: `{doc['error']}`")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Command(BaseCommand):
    help = "Run current matchup segmentation on fixture files, read-only, DB write 0."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            dest="files",
            action="append",
            default=[],
            help="File to evaluate. Repeatable.",
        )
        parser.add_argument(
            "--dir",
            dest="input_dir",
            type=str,
            help="Directory to scan recursively for PDFs/images.",
        )
        parser.add_argument(
            "--output",
            type=str,
            help="Artifact output directory. Defaults to workspace _artifacts.",
        )
        parser.add_argument(
            "--source-type",
            type=str,
            choices=SOURCE_TYPES,
            default="other",
            help="Source type routed into the segmentation dispatcher.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of files to evaluate.",
        )
        parser.add_argument(
            "--overlay-limit-pages",
            type=int,
            default=12,
            help="Maximum overlay pages per file.",
        )
        parser.add_argument(
            "--no-overlays",
            action="store_true",
            help="Disable page overlay PNG generation.",
        )
        parser.add_argument(
            "--dispatcher-only",
            action="store_true",
            help=(
                "Bypass product source_type indexing skip and call the raw "
                "segmentation dispatcher."
            ),
        )

    def handle(self, *args, **options):
        from academy.adapters.ai.detection.segment_dispatcher import (
            cleanup_pdf_seg_tmp_dirs,
            segment_questions_multipage,
        )

        source_type = normalize_source_type(options.get("source_type"))
        targets = _collect_targets(
            list(options.get("files") or []),
            options.get("input_dir"),
        )
        limit = options.get("limit")
        if limit is not None:
            if limit <= 0:
                raise CommandError("--limit must be positive")
            targets = targets[:limit]
        if not targets:
            raise CommandError("--file or --dir must select at least one file")

        overlay_limit_pages = int(options.get("overlay_limit_pages") or 0)
        if overlay_limit_pages < 0:
            raise CommandError("--overlay-limit-pages must be >= 0")

        out_dir = Path(options.get("output") or _default_output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        docs_dir = out_dir / "documents"
        docs_dir.mkdir(parents=True, exist_ok=True)
        overlay_dir = None
        if not options.get("no_overlays") and overlay_limit_pages > 0:
            overlay_dir = out_dir / "overlays"

        self.stdout.write(self.style.NOTICE(
            f"matchup golden eval: {len(targets)} file(s) -> {out_dir}"
        ))
        self.stdout.write(self.style.WARNING(
            "read-only: DB write 0, R2 write 0, hit report write 0"
        ))

        results: list[dict[str, Any]] = []
        for idx, target in enumerate(targets, start=1):
            self.stdout.write(f"[{idx}/{len(targets)}] {target}")
            raw_result: dict[str, Any] | None = None
            try:
                if not options.get("dispatcher_only") and not is_indexable(source_type):
                    doc_result = _skipped_document_metrics(
                        target,
                        source_type=source_type,
                    )
                else:
                    raw_result = segment_questions_multipage(
                        str(target),
                        source_type=source_type,
                    )
                    doc_result = _document_metrics(
                        target,
                        raw_result,
                        source_type=source_type,
                        overlay_dir=overlay_dir,
                        overlay_limit_pages=overlay_limit_pages,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception("matchup golden eval failed: %s", target)
                doc_result = {
                    "input": str(target),
                    "filename": target.name,
                    "source_type": source_type,
                    "ok": False,
                    "error": repr(exc),
                    "page_count": 0,
                    "total_boxes": 0,
                    "quality_flag_counts": {"eval_exception": 1},
                    "paper_type_distribution": {},
                    "pages": [],
                }
                doc_result["quality_grade"] = _quality_grade(
                    {"eval_exception": 1},
                    ok=False,
                )
            finally:
                if raw_result:
                    cleanup_pdf_seg_tmp_dirs(list(raw_result.get("tmp_dirs") or []))

            slug = _safe_slug(target.stem)
            doc_path = docs_dir / f"{slug}.json"
            doc_result["artifact_path"] = str(doc_path)
            doc_path.write_text(
                json.dumps(doc_result, ensure_ascii=False, indent=2, default=_json_default),
                encoding="utf-8",
            )
            results.append(doc_result)

            flag_text = doc_result.get("quality_flag_counts") or {}
            self.stdout.write(
                f"  pages={doc_result.get('page_count', 0)} "
                f"boxes={doc_result.get('total_boxes', 0)} flags={flag_text}"
            )

        aggregate_flags = Counter(
            flag
            for doc in results
            for flag, count in (doc.get("quality_flag_counts") or {}).items()
            for _ in range(int(count))
        )
        aggregate_paper_types = Counter(
            paper_type
            for doc in results
            for paper_type, count in (doc.get("paper_type_distribution") or {}).items()
            for _ in range(int(count))
        )
        grade_counts = Counter(
            (doc.get("quality_grade") or {}).get("status", "unknown")
            for doc in results
        )
        summary = {
            "schema_version": SCHEMA_VERSION,
            "ran_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_type": source_type,
            "dry_run": True,
            "db_writes": 0,
            "r2_writes": 0,
            "hit_report_writes": 0,
            "aggregate": {
                "total_files": len(results),
                "ok_files": sum(1 for result in results if result.get("ok")),
                "failed_files": sum(1 for result in results if not result.get("ok")),
                "skipped_for_indexing_files": sum(
                    1 for result in results if result.get("skipped_for_indexing")
                ),
                "page_count": sum(int(result.get("page_count") or 0) for result in results),
                "total_boxes": sum(int(result.get("total_boxes") or 0) for result in results),
                "text_page_count": sum(
                    int(result.get("text_page_count") or 0) for result in results
                ),
                "skip_page_count": sum(
                    int(result.get("skip_page_count") or 0) for result in results
                ),
                "empty_page_count": sum(
                    int(result.get("empty_page_count") or 0) for result in results
                ),
                "paper_type_distribution": dict(sorted(aggregate_paper_types.items())),
                "quality_flag_counts": dict(sorted(aggregate_flags.items())),
                "quality_grade_counts": dict(sorted(grade_counts.items())),
            },
            "results": results,
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
