"""Evaluate matchup segmentation against manual-crop ground truth.

This command is intentionally read-only for product state. It reads a JSON
manifest exported from production/API data, runs the current local segmentation
dispatcher against local copies of the original files, and writes inspectable
artifacts under _artifacts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "matchup_manual_gt_eval.v1"
SUPPORTED_SUFFIXES = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
})
DEFAULT_IOU_THRESHOLD = 0.50
DEFAULT_MIN_RECALL = 0.98
DEFAULT_MIN_PRECISION = 0.95
ENGINE_DISPATCHER = "dispatcher"
ENGINE_NATIVE_V54 = "native-v5-4"
ENGINE_NATIVE_V55 = "native-v5-5"
ENGINE_CHOICES = (ENGINE_DISPATCHER, ENGINE_NATIVE_V54, ENGINE_NATIVE_V55)


@dataclass(frozen=True)
class NormBox:
    x: float
    y: float
    w: float
    h: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)


@dataclass(frozen=True)
class GroundTruthBox:
    index: int
    number: int | None
    page_index: int
    bbox: NormBox
    problem_id: int | None = None


@dataclass(frozen=True)
class PredictedBox:
    index: int
    number: int | None
    page_index: int
    bbox: NormBox
    raw_box: tuple[int, int, int, int]


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[6]


def _default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return (
        _workspace_root()
        / "_artifacts"
        / "sessions"
        / "matchup-manual-gt-eval"
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
    if isinstance(value, (NormBox, GroundTruthBox, PredictedBox)):
        return value.__dict__
    return str(value)


def _coerce_norm_box(raw: Any) -> NormBox | None:
    if isinstance(raw, dict):
        values = [raw.get("x"), raw.get("y"), raw.get("w"), raw.get("h")]
    elif isinstance(raw, (list, tuple)) and len(raw) >= 4:
        values = list(raw[:4])
    else:
        return None
    try:
        x, y, w, h = (float(v) for v in values)
    except (TypeError, ValueError):
        return None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
        return None
    if x + w > 1.01 or y + h > 1.01:
        return None
    return NormBox(x=x, y=y, w=w, h=h)


def _box_iou(a: NormBox, b: NormBox) -> float:
    ax1, ay1 = a.x, a.y
    ax2, ay2 = a.x + a.w, a.y + a.h
    bx1, by1 = b.x, b.y
    bx2, by2 = b.x + b.w, b.y + b.h
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a.w) * max(0.0, a.h)
    area_b = max(0.0, b.w) * max(0.0, b.h)
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return max(0.0, min(1.0, inter / union))


def _extract_ground_truth(document: dict[str, Any]) -> list[GroundTruthBox]:
    out: list[GroundTruthBox] = []
    for idx, row in enumerate(document.get("ground_truth") or []):
        if not isinstance(row, dict):
            continue
        bbox = _coerce_norm_box(row.get("bbox_norm") or row.get("corrected_bbox"))
        if bbox is None:
            continue
        try:
            page_index = int(row.get("page_index"))
        except (TypeError, ValueError):
            continue
        try:
            number = int(row.get("number")) if row.get("number") is not None else None
        except (TypeError, ValueError):
            number = None
        try:
            problem_id = int(row.get("problem_id")) if row.get("problem_id") is not None else None
        except (TypeError, ValueError):
            problem_id = None
        out.append(GroundTruthBox(
            index=idx,
            number=number,
            page_index=page_index,
            bbox=bbox,
            problem_id=problem_id,
        ))
    return out


def _image_size(page: dict[str, Any]) -> tuple[int, int] | None:
    try:
        w = int(page.get("image_width") or 0)
        h = int(page.get("image_height") or 0)
        if w > 0 and h > 0:
            return w, h
    except (TypeError, ValueError):
        pass
    image_path = page.get("image_path")
    if not image_path:
        return None
    try:
        from PIL import Image

        with Image.open(str(image_path)) as img:
            return int(img.width), int(img.height)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read page image size: %s", exc)
        return None


def _extract_predictions(result: dict[str, Any]) -> list[PredictedBox]:
    predictions: list[PredictedBox] = []
    pages = result.get("pages") or []
    if not isinstance(pages, list):
        return predictions

    pred_index = 0
    for fallback_page_index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        try:
            page_index = int(page.get("page_index", fallback_page_index))
        except (TypeError, ValueError):
            page_index = fallback_page_index
        size = _image_size(page)
        if not size:
            continue
        img_w, img_h = size
        boxes = page.get("boxes") or []
        numbers = page.get("numbers") or []
        if not isinstance(boxes, list):
            continue
        if not isinstance(numbers, list):
            numbers = []
        for box_idx, raw in enumerate(boxes):
            if not isinstance(raw, (list, tuple)) or len(raw) != 4:
                continue
            try:
                x, y, w, h = (int(float(v)) for v in raw)
            except (TypeError, ValueError):
                continue
            if img_w <= 0 or img_h <= 0 or w <= 0 or h <= 0:
                continue
            norm = NormBox(
                x=max(0.0, x / img_w),
                y=max(0.0, y / img_h),
                w=min(1.0, w / img_w),
                h=min(1.0, h / img_h),
            )
            number_raw = numbers[box_idx] if box_idx < len(numbers) else None
            try:
                number = int(number_raw) if number_raw is not None else None
            except (TypeError, ValueError):
                number = None
            predictions.append(PredictedBox(
                index=pred_index,
                number=number,
                page_index=page_index,
                bbox=norm,
                raw_box=(x, y, w, h),
            ))
            pred_index += 1
    return predictions


def _native_pdf_result_to_multipage(
    source_path: Path,
    native_result: dict[str, Any],
    *,
    render_pages: bool,
) -> dict[str, Any]:
    """Adapt tier0_native_pdf output to the dispatcher multipage shape.

    The evaluator compares normalized boxes, but overlay QA still needs page
    images. Keep this adapter local to the read-only command so the production
    runtime is not changed merely to inspect an experimental engine.
    """
    from academy.adapters.tools.pymupdf_renderer import PdfDocument

    native_pages = native_result.get("pages") or []
    if not isinstance(native_pages, list):
        native_pages = []
    native_by_page: dict[int, dict[str, Any]] = {}
    for fallback_idx, page in enumerate(native_pages):
        if not isinstance(page, dict):
            continue
        try:
            page_index = int(page.get("page_index", fallback_idx))
        except (TypeError, ValueError):
            page_index = fallback_idx
        native_by_page[page_index] = page

    tmp_dir = Path(tempfile.mkdtemp(prefix="pdf-seg-native-eval-")) if render_pages else None
    pages: list[dict[str, Any]] = []
    total = 0
    with PdfDocument(str(source_path)) as doc:
        for page_index in range(doc.page_count()):
            image_path = None
            if render_pages:
                pil_img = doc.render_page(page_index, dpi=200)
                image_path = tmp_dir / f"page_{page_index:03d}.png"
                pil_img.save(image_path, "PNG")
                img_w, img_h = int(pil_img.width), int(pil_img.height)
            else:
                page_rect = doc.page_dimensions(page_index)
                img_w, img_h = int(round(page_rect[0] * 10)), int(round(page_rect[1] * 10))
            native_page = native_by_page.get(page_index, {})
            candidates = native_page.get("bbox_candidates") or []
            if not isinstance(candidates, list):
                candidates = []
            boxes: list[tuple[int, int, int, int]] = []
            numbers: list[int | None] = []
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                bbox = _coerce_norm_box(cand.get("bbox_norm"))
                if bbox is None:
                    continue
                x = max(0, min(img_w - 1, int(round(bbox.x * img_w))))
                y = max(0, min(img_h - 1, int(round(bbox.y * img_h))))
                x2 = max(x + 1, min(img_w, int(round((bbox.x + bbox.w) * img_w))))
                y2 = max(y + 1, min(img_h, int(round((bbox.y + bbox.h) * img_h))))
                boxes.append((x, y, x2 - x, y2 - y))
                try:
                    number = int(cand.get("number")) if cand.get("number") is not None else None
                except (TypeError, ValueError):
                    number = None
                numbers.append(number)
            total += len(boxes)
            pages.append({
                "page_index": page_index,
                "image_path": str(image_path) if image_path is not None else "",
                "image_width": img_w,
                "image_height": img_h,
                "boxes": boxes,
                "numbers": numbers,
                "has_embedded_text": bool(native_page.get("has_embedded_text")),
                "is_skip_page": native_page.get("role") in {"cover", "answer_key", "index"},
                "paper_type": native_result.get("_internal_paper_type") or "unknown",
                "paper_type_debug": {
                    "native_version": native_result.get("version"),
                    "role": native_page.get("role"),
                    "role_confidence": native_page.get("role_confidence"),
                },
                "page_text": "",
            })

    return {
        "pages": pages,
        "total_boxes": total,
        "is_pdf": True,
        "tmp_dirs": [str(tmp_dir)] if tmp_dir is not None else [],
        "native_result": native_result,
    }


def _match_page_boxes(
    gt_boxes: list[GroundTruthBox],
    pred_boxes: list[PredictedBox],
    *,
    iou_threshold: float,
) -> tuple[list[dict[str, Any]], list[GroundTruthBox], list[PredictedBox]]:
    pairs: list[tuple[float, GroundTruthBox, PredictedBox]] = []
    for gt in gt_boxes:
        for pred in pred_boxes:
            iou = _box_iou(gt.bbox, pred.bbox)
            if iou >= iou_threshold:
                pairs.append((iou, gt, pred))
    pairs.sort(key=lambda item: item[0], reverse=True)

    used_gt: set[int] = set()
    used_pred: set[int] = set()
    matches: list[dict[str, Any]] = []
    for iou, gt, pred in pairs:
        if gt.index in used_gt or pred.index in used_pred:
            continue
        used_gt.add(gt.index)
        used_pred.add(pred.index)
        matches.append({
            "gt_index": gt.index,
            "gt_problem_id": gt.problem_id,
            "gt_number": gt.number,
            "pred_index": pred.index,
            "pred_number": pred.number,
            "page_index": gt.page_index,
            "iou": round(iou, 6),
            "number_match": gt.number is not None and pred.number == gt.number,
        })

    missed = [gt for gt in gt_boxes if gt.index not in used_gt]
    extra = [pred for pred in pred_boxes if pred.index not in used_pred]
    return matches, missed, extra


def evaluate_predictions_against_gt(
    gt_boxes: list[GroundTruthBox],
    pred_boxes: list[PredictedBox],
    *,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    min_recall: float = DEFAULT_MIN_RECALL,
    min_precision: float = DEFAULT_MIN_PRECISION,
) -> dict[str, Any]:
    gt_by_page: dict[int, list[GroundTruthBox]] = defaultdict(list)
    pred_by_page: dict[int, list[PredictedBox]] = defaultdict(list)
    for gt in gt_boxes:
        gt_by_page[gt.page_index].append(gt)
    for pred in pred_boxes:
        pred_by_page[pred.page_index].append(pred)

    matches: list[dict[str, Any]] = []
    missed: list[GroundTruthBox] = []
    extra: list[PredictedBox] = []
    pages = sorted(set(gt_by_page) | set(pred_by_page))
    page_summaries: list[dict[str, Any]] = []
    for page_index in pages:
        page_matches, page_missed, page_extra = _match_page_boxes(
            gt_by_page.get(page_index, []),
            pred_by_page.get(page_index, []),
            iou_threshold=iou_threshold,
        )
        matches.extend(page_matches)
        missed.extend(page_missed)
        extra.extend(page_extra)
        page_summaries.append({
            "page_index": page_index,
            "gt_count": len(gt_by_page.get(page_index, [])),
            "pred_count": len(pred_by_page.get(page_index, [])),
            "matched_count": len(page_matches),
            "missed_count": len(page_missed),
            "extra_count": len(page_extra),
        })

    gt_count = len(gt_boxes)
    pred_count = len(pred_boxes)
    matched_count = len(matches)
    recall = matched_count / gt_count if gt_count else 0.0
    precision = matched_count / pred_count if pred_count else 0.0
    mean_iou = (
        sum(float(m["iou"]) for m in matches) / matched_count
        if matched_count
        else 0.0
    )
    number_match_count = sum(1 for m in matches if m.get("number_match"))
    status = "pass" if (
        gt_count > 0
        and recall >= min_recall
        and precision >= min_precision
    ) else "fail"

    return {
        "status": status,
        "gt_count": gt_count,
        "pred_count": pred_count,
        "matched_count": matched_count,
        "missed_count": len(missed),
        "extra_count": len(extra),
        "recall": round(recall, 6),
        "precision": round(precision, 6),
        "mean_iou": round(mean_iou, 6),
        "number_match_count": number_match_count,
        "number_match_ratio": round(number_match_count / matched_count, 6) if matched_count else 0.0,
        "matches": matches,
        "missed": [
            {
                "gt_index": gt.index,
                "problem_id": gt.problem_id,
                "number": gt.number,
                "page_index": gt.page_index,
                "bbox": gt.bbox.as_tuple(),
            }
            for gt in missed
        ],
        "extra": [
            {
                "pred_index": pred.index,
                "number": pred.number,
                "page_index": pred.page_index,
                "bbox": pred.bbox.as_tuple(),
                "raw_box": pred.raw_box,
            }
            for pred in extra
        ],
        "ground_truth": [
            {
                "gt_index": gt.index,
                "problem_id": gt.problem_id,
                "number": gt.number,
                "page_index": gt.page_index,
                "bbox": gt.bbox.as_tuple(),
            }
            for gt in gt_boxes
        ],
        "predictions": [
            {
                "pred_index": pred.index,
                "number": pred.number,
                "page_index": pred.page_index,
                "bbox": pred.bbox.as_tuple(),
                "raw_box": pred.raw_box,
            }
            for pred in pred_boxes
        ],
        "pages": page_summaries,
    }


def _find_original_file(input_dir: Path, doc_id: int) -> Path | None:
    candidates: list[Path] = []
    for stem in (f"doc-{doc_id}", f"doc_{doc_id}", str(doc_id)):
        for suffix in SUPPORTED_SUFFIXES:
            candidates.append(input_dir / f"{stem}{suffix}")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    globbed = sorted(
        p for p in input_dir.glob(f"doc-{doc_id}.*")
        if p.suffix.lower() in SUPPORTED_SUFFIXES and p.is_file()
    )
    if globbed:
        return globbed[0]
    return None


def _draw_overlay(
    page: dict[str, Any],
    gt_boxes: list[GroundTruthBox],
    pred_boxes: list[PredictedBox],
    matches: list[dict[str, Any]],
    output_path: Path,
) -> str | None:
    image_path = page.get("image_path")
    if not image_path:
        return "missing image_path"
    try:
        from PIL import Image, ImageDraw, ImageFont

        with Image.open(str(image_path)) as source:
            img = source.convert("RGB")
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        line_width = max(2, min(img.width, img.height) // 320)
        matched_gt = {int(m["gt_index"]) for m in matches}
        matched_pred = {int(m["pred_index"]) for m in matches}

        def to_px(box: NormBox) -> tuple[int, int, int, int]:
            x1 = int(round(box.x * img.width))
            y1 = int(round(box.y * img.height))
            x2 = int(round((box.x + box.w) * img.width))
            y2 = int(round((box.y + box.h) * img.height))
            return x1, y1, max(x1 + 1, x2), max(y1 + 1, y2)

        for gt in gt_boxes:
            color = (20, 150, 70) if gt.index in matched_gt else (220, 45, 45)
            rect = to_px(gt.bbox)
            draw.rectangle(rect, outline=color, width=line_width)
            label = f"GT{gt.number if gt.number is not None else '?'}"
            draw.text((rect[0], max(0, rect[1] - 12)), label, fill=color, font=font)

        for pred in pred_boxes:
            color = (35, 105, 210) if pred.index in matched_pred else (230, 145, 30)
            rect = to_px(pred.bbox)
            draw.rectangle(rect, outline=color, width=max(1, line_width // 2))
            label = f"P{pred.number if pred.number is not None else '?'}"
            draw.text((rect[0], min(img.height - 12, rect[3] + 2)), label, fill=color, font=font)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path)
        return None
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def _write_report(summary: dict[str, Any], report_path: Path) -> None:
    agg = summary["aggregate"]
    lines = [
        "# Matchup Manual GT Eval",
        "",
        f"- Ran at UTC: `{summary['ran_at_utc']}`",
        f"- Manifest: `{summary['manifest']}`",
        f"- Input dir: `{summary['input_dir']}`",
        "- Mode: read-only, DB write 0, R2 write 0",
        f"- IoU threshold: `{summary['iou_threshold']}`",
        f"- Files evaluated: `{agg['evaluated_docs']}/{agg['selected_docs']}`",
        f"- GT boxes: `{agg['gt_count']}`",
        f"- Predicted boxes: `{agg['pred_count']}`",
        f"- Matched: `{agg['matched_count']}`",
        f"- Missed: `{agg['missed_count']}`",
        f"- Extra: `{agg['extra_count']}`",
        f"- Recall: `{agg['recall']}`",
        f"- Precision: `{agg['precision']}`",
        f"- Mean IoU: `{agg['mean_iou']}`",
        "",
        "## Documents",
        "",
    ]
    for doc in summary["results"]:
        if doc.get("status") == "file_missing":
            lines.append(
                f"- `file_missing` doc#{doc['doc_id']} `{doc.get('title', '')}`"
            )
            continue
        lines.append(
            f"- `{doc.get('status')}` doc#{doc['doc_id']} "
            f"`{doc.get('title', '')}`: gt={doc.get('gt_count', 0)} "
            f"pred={doc.get('pred_count', 0)} matched={doc.get('matched_count', 0)} "
            f"missed={doc.get('missed_count', 0)} extra={doc.get('extra_count', 0)} "
            f"recall={doc.get('recall', 0)} precision={doc.get('precision', 0)} "
            f"mean_iou={doc.get('mean_iou', 0)}"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Command(BaseCommand):
    help = "Read-only manual GT evaluator for matchup segmentation."

    def add_arguments(self, parser):
        parser.add_argument("--manifest", required=True, help="Manual GT manifest JSON path.")
        parser.add_argument("--input-dir", required=True, help="Directory containing original files named doc-<id>.*.")
        parser.add_argument("--output", default="", help="Artifact output directory.")
        parser.add_argument("--doc-id", action="append", type=int, default=[], help="Limit to doc id. Repeatable.")
        parser.add_argument("--limit", type=int, default=None, help="Limit selected documents after filtering.")
        parser.add_argument("--iou-threshold", type=float, default=DEFAULT_IOU_THRESHOLD)
        parser.add_argument("--min-recall", type=float, default=DEFAULT_MIN_RECALL)
        parser.add_argument("--min-precision", type=float, default=DEFAULT_MIN_PRECISION)
        parser.add_argument("--overlay-limit-pages", type=int, default=8)
        parser.add_argument("--include-student-photo", action="store_true", help="Do not exclude student_exam_photo docs.")
        parser.add_argument("--no-overlays", action="store_true", help="Disable overlay PNG generation.")
        parser.add_argument(
            "--engine",
            choices=ENGINE_CHOICES,
            default=ENGINE_DISPATCHER,
            help="Segmentation engine to evaluate. Default: dispatcher.",
        )

    def handle(self, *args, **options):
        from academy.adapters.ai.detection.segment_dispatcher import (
            cleanup_pdf_seg_tmp_dirs,
            segment_questions_multipage,
        )
        from academy.adapters.ai.detection.tier0_native_pdf import (
            analyze_pdf_v5_4,
            analyze_pdf_v5_5,
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
        overlay_limit_pages = int(options["overlay_limit_pages"])
        if overlay_limit_pages < 0:
            raise CommandError("--overlay-limit-pages must be >= 0")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        documents = manifest.get("documents") or []
        if not isinstance(documents, list):
            raise CommandError("manifest.documents must be a list")

        doc_ids = set(options.get("doc_id") or [])
        selected: list[dict[str, Any]] = []
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            try:
                doc_id = int(doc["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if doc_ids and doc_id not in doc_ids:
                continue
            if not options["include_student_photo"] and doc.get("meta_source_type") == "student_exam_photo":
                continue
            if not doc.get("ground_truth"):
                continue
            selected.append(doc)

        if options.get("limit") is not None:
            limit = int(options["limit"])
            if limit <= 0:
                raise CommandError("--limit must be positive")
            selected = selected[:limit]
        if not selected:
            raise CommandError("no documents selected")
        engine = str(options["engine"])

        out_dir = Path(options.get("output") or _default_output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        docs_dir = out_dir / "documents"
        docs_dir.mkdir(parents=True, exist_ok=True)
        overlay_root = None if options["no_overlays"] else out_dir / "overlays"

        self.stdout.write(self.style.NOTICE(
            f"manual GT eval: docs={len(selected)} engine={engine} input={input_dir} -> {out_dir}"
        ))
        self.stdout.write(self.style.WARNING("read-only: DB write 0, R2 write 0"))

        results: list[dict[str, Any]] = []
        for idx, doc in enumerate(selected, start=1):
            doc_id = int(doc["id"])
            title = str(doc.get("title") or "")
            source_path = _find_original_file(input_dir, doc_id)
            if source_path is None:
                result_doc = {
                    "doc_id": doc_id,
                    "title": title,
                    "status": "file_missing",
                    "gt_count": len(_extract_ground_truth(doc)),
                }
                results.append(result_doc)
                self.stdout.write(f"[{idx}/{len(selected)}] doc#{doc_id} file_missing")
                continue

            raw_result: dict[str, Any] | None = None
            try:
                if engine == ENGINE_DISPATCHER:
                    raw_result = segment_questions_multipage(
                        str(source_path),
                        source_type=str(doc.get("meta_source_type") or "other"),
                    )
                elif engine == ENGINE_NATIVE_V54:
                    native = analyze_pdf_v5_4(
                        str(source_path),
                        file_name=source_path.name,
                    )
                    raw_result = _native_pdf_result_to_multipage(
                        source_path,
                        native,
                        render_pages=overlay_root is not None and overlay_limit_pages > 0,
                    )
                elif engine == ENGINE_NATIVE_V55:
                    native = analyze_pdf_v5_5(
                        str(source_path),
                        file_name=source_path.name,
                    )
                    raw_result = _native_pdf_result_to_multipage(
                        source_path,
                        native,
                        render_pages=overlay_root is not None and overlay_limit_pages > 0,
                    )
                else:
                    raise CommandError(f"unsupported engine: {engine}")
                gt_boxes = _extract_ground_truth(doc)
                pred_boxes = _extract_predictions(raw_result)
                metrics = evaluate_predictions_against_gt(
                    gt_boxes,
                    pred_boxes,
                    iou_threshold=iou_threshold,
                    min_recall=float(options["min_recall"]),
                    min_precision=float(options["min_precision"]),
                )
                result_doc = {
                    "doc_id": doc_id,
                    "title": title,
                    "source_type": doc.get("meta_source_type") or "",
                    "paper_primary": doc.get("paper_primary") or "",
                    "engine": engine,
                    "engine_version": (
                        (raw_result.get("native_result") or {}).get("version")
                        if isinstance(raw_result.get("native_result"), dict)
                        else engine
                    ),
                    "input": str(source_path),
                    "is_pdf": bool(raw_result.get("is_pdf")),
                    "page_count": len(raw_result.get("pages") or []),
                    **metrics,
                }

                if overlay_root is not None and overlay_limit_pages > 0:
                    by_page_gt: dict[int, list[GroundTruthBox]] = defaultdict(list)
                    by_page_pred: dict[int, list[PredictedBox]] = defaultdict(list)
                    by_page_matches: dict[int, list[dict[str, Any]]] = defaultdict(list)
                    for gt in gt_boxes:
                        by_page_gt[gt.page_index].append(gt)
                    for pred in pred_boxes:
                        by_page_pred[pred.page_index].append(pred)
                    for match in metrics["matches"]:
                        by_page_matches[int(match["page_index"])].append(match)
                    overlay_errors: list[dict[str, Any]] = []
                    overlay_paths: list[str] = []
                    pages = raw_result.get("pages") or []
                    rendered = 0
                    doc_slug = f"doc-{doc_id}-{_safe_slug(title)}"
                    for page in pages:
                        if rendered >= overlay_limit_pages:
                            break
                        try:
                            page_index = int(page.get("page_index", rendered))
                        except (TypeError, ValueError):
                            page_index = rendered
                        if not by_page_gt.get(page_index) and not by_page_pred.get(page_index):
                            continue
                        out_path = overlay_root / doc_slug / f"page_{page_index:03d}.png"
                        err = _draw_overlay(
                            page,
                            by_page_gt.get(page_index, []),
                            by_page_pred.get(page_index, []),
                            by_page_matches.get(page_index, []),
                            out_path,
                        )
                        if err:
                            overlay_errors.append({"page_index": page_index, "error": err})
                        else:
                            overlay_paths.append(str(out_path))
                        rendered += 1
                    result_doc["overlay_paths"] = overlay_paths
                    result_doc["overlay_errors"] = overlay_errors
            except Exception as exc:  # noqa: BLE001
                logger.exception("manual GT eval failed for doc %s", doc_id)
                result_doc = {
                    "doc_id": doc_id,
                    "title": title,
                    "status": "eval_exception",
                    "error": repr(exc),
                    "gt_count": len(_extract_ground_truth(doc)),
                }
            finally:
                if raw_result:
                    cleanup_pdf_seg_tmp_dirs(list(raw_result.get("tmp_dirs") or []))

            doc_path = docs_dir / f"doc-{doc_id}.json"
            result_doc["artifact_path"] = str(doc_path)
            doc_path.write_text(
                json.dumps(result_doc, ensure_ascii=False, indent=2, default=_json_default),
                encoding="utf-8",
            )
            results.append(result_doc)
            self.stdout.write(
                f"[{idx}/{len(selected)}] doc#{doc_id} {result_doc.get('status')} "
                f"gt={result_doc.get('gt_count', 0)} pred={result_doc.get('pred_count', 0)} "
                f"matched={result_doc.get('matched_count', 0)} missed={result_doc.get('missed_count', 0)} "
                f"extra={result_doc.get('extra_count', 0)}"
            )

        evaluated = [r for r in results if r.get("status") not in {"file_missing", "eval_exception"}]
        gt_count = sum(int(r.get("gt_count") or 0) for r in evaluated)
        pred_count = sum(int(r.get("pred_count") or 0) for r in evaluated)
        matched_count = sum(int(r.get("matched_count") or 0) for r in evaluated)
        missed_count = sum(int(r.get("missed_count") or 0) for r in evaluated)
        extra_count = sum(int(r.get("extra_count") or 0) for r in evaluated)
        mean_iou = (
            sum(float(r.get("mean_iou") or 0) * int(r.get("matched_count") or 0) for r in evaluated)
            / matched_count
            if matched_count
            else 0.0
        )
        status_counts = Counter(str(r.get("status") or "unknown") for r in results)
        summary = {
            "schema_version": SCHEMA_VERSION,
            "ran_at_utc": datetime.now(timezone.utc).isoformat(),
            "manifest": str(manifest_path),
            "input_dir": str(input_dir),
            "output_dir": str(out_dir),
            "dry_run": True,
            "db_writes": 0,
            "r2_writes": 0,
            "engine": engine,
            "iou_threshold": iou_threshold,
            "min_recall": float(options["min_recall"]),
            "min_precision": float(options["min_precision"]),
            "aggregate": {
                "selected_docs": len(selected),
                "evaluated_docs": len(evaluated),
                "status_counts": dict(sorted(status_counts.items())),
                "gt_count": gt_count,
                "pred_count": pred_count,
                "matched_count": matched_count,
                "missed_count": missed_count,
                "extra_count": extra_count,
                "recall": round(matched_count / gt_count, 6) if gt_count else 0.0,
                "precision": round(matched_count / pred_count, 6) if pred_count else 0.0,
                "mean_iou": round(mean_iou, 6),
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
