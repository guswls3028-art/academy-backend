from __future__ import annotations

import base64
import csv
import html
import io
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

from django.http import HttpResponse

from apps.domains.tools.problem_studio.extractors import (
    extract_docx_text,
    extract_hwp_text as extract_hwp_text_only,
    extract_hwp_text_and_images,
    extract_hwpx_document_profile,
    extract_hwpx_question_images,
    extract_hwpx_text,
    normalize_hwp_image_data,
    safe_zip_members,
)
from apps.domains.tools.problem_studio.document_style import (
    DEFAULT_PAGE_LAYOUT,
    custom_font_snapshots,
)
from apps.domains.tools.problem_studio.hwpx_writer import (
    build_hwpx_exam_document,
    build_hwpx_source_fidelity_document,
)
from apps.domains.tools.problem_studio.ocr import (
    OcrResult,
    extract_ocr_text_from_image,
    problem_studio_ocr_enabled,
    problem_studio_ocr_max_units,
)
from apps.domains.tools.problem_studio.structure import (
    TransferStructure,
    apply_generated_explanations,
    analyze_transfer_documents,
    normalize_space,
)
from apps.domains.tools.problem_studio.voice_profiles import (
    augment_voice_profile_with_source_items,
)


TRANSFER_MAX_UPLOAD_BYTES = 120 * 1024 * 1024
TRANSFER_MAX_ZIP_MEMBERS = 300
TRANSFER_MAX_ZIP_UNCOMPRESSED_BYTES = 180 * 1024 * 1024
TRANSFER_PDF_PAGES_PER_DOC = 60
TRANSFER_PDF_RENDER_ZOOM = 1.45
TRANSFER_HWP_GALLERY_IMAGES_PER_ROW = 1
SUPPORTED_TRANSFER_SUFFIXES = {
    ".pdf",
    ".hwp",
    ".hwpx",
    ".docx",
    ".doc",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
}


@dataclass
class SourcePageVisual:
    source_name: str
    page_number: int
    mime: str
    data: bytes = field(repr=False)
    width_px: int = 0
    height_px: int = 0
    page_width_pt: float = 0.0
    page_height_pt: float = 0.0


@dataclass
class QuestionVisual:
    source_name: str
    page_number: int
    question_number: int
    mime: str
    data: bytes = field(repr=False)
    width_px: int = 0
    height_px: int = 0
    semantic_flags: tuple[str, ...] = ()
    bbox: tuple[float, float, float, float] = ()
    role: str = "question_crop"


@dataclass
class TransferDocument:
    filename: str
    html: str
    source_name: str
    kind: str
    text_chars: int = 0
    image_count: int = 0
    page_count: int = 0
    page_start: int = 0
    page_end: int = 0
    warning: str | None = None
    plain_text: str = ""
    ocr_text_chars: int = 0
    ocr_completed_units: int = 0
    ocr_pending_units: int = 0
    ocr_status: str = "not_applicable"
    ocr_engine: str = ""
    ocr_warning: str | None = None
    page_width_pt: float = 0.0
    page_height_pt: float = 0.0
    detected_column_count: int = 0
    layout_source: str = ""
    source_pages: list[SourcePageVisual] = field(default_factory=list, repr=False)
    question_visuals: list[QuestionVisual] = field(default_factory=list, repr=False)
    document_style_hint: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class TransferPackage:
    filename: str
    content_type: str
    data: bytes
    documents: list[TransferDocument] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    review_file_count: int = 0
    structured_item_count: int = 0
    ocr_candidate_count: int = 0
    quality_level: str = ""
    structure_limit_reached: bool = False
    font_assets: list[dict[str, Any]] = field(default_factory=list)
    explanation_count: int = 0
    detected_layout: dict[str, Any] = field(default_factory=dict)
    reconstruction_quality: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransferOcrContext:
    enabled: bool = field(default_factory=problem_studio_ocr_enabled)
    max_units: int = field(default_factory=problem_studio_ocr_max_units)
    used_units: int = 0
    disabled_reason: str = ""
    extractor: Callable[[bytes, str], OcrResult] | None = field(default=None, repr=False)

    @property
    def remaining_units(self) -> int:
        if not self.enabled:
            return 0
        return max(0, self.max_units - self.used_units)

    def extract(self, data: bytes, *, mime: str) -> OcrResult:
        if not self.enabled:
            return OcrResult(text="", status="disabled", warning="OCR 비활성화")
        if self.disabled_reason:
            return OcrResult(text="", status="unavailable", warning=self.disabled_reason)
        if self.used_units >= self.max_units:
            return OcrResult(text="", status="skipped_limit", warning="자동 OCR 처리 한도 초과")
        self.used_units += 1
        result = self.extractor(data, mime) if self.extractor else extract_ocr_text_from_image(data, mime=mime)
        if result.status in {"unavailable", "disabled"}:
            self.disabled_reason = result.warning or "OCR 엔진을 사용할 수 없음"
        return result


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


_SUBSCRIPT_CHARS = frozenset("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ")
_SUPERSCRIPT_CHARS = frozenset("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿ")
_SUBSCRIPT_HTML_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎", "0123456789+-=()")
_SUPERSCRIPT_HTML_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾", "0123456789+-=()")


def _rich_text_html(value: Any) -> str:
    """Escape text while retaining semantic sub/superscript runs in HTML/DOC."""
    output: list[str] = []
    active_tag = ""
    for char in str(value or ""):
        tag = "sub" if char in _SUBSCRIPT_CHARS else "sup" if char in _SUPERSCRIPT_CHARS else ""
        if tag != active_tag:
            if active_tag:
                output.append(f"</{active_tag}>")
            if tag:
                output.append(f"<{tag}>")
            active_tag = tag
        normalized_char = (
            char.translate(_SUBSCRIPT_HTML_MAP)
            if tag == "sub"
            else char.translate(_SUPERSCRIPT_HTML_MAP)
            if tag == "sup"
            else char
        )
        output.append(html.escape(normalized_char, quote=True))
    if active_tag:
        output.append(f"</{active_tag}>")
    return "".join(output)


def _css_string(value: Any) -> str:
    escaped = (
        str(value or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("<", "\\3C ")
        .replace(">", "\\3E ")
    )
    return f'"{escaped}"'


def _safe_document_style(resolved: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(resolved, dict):
        return None
    safe = {
        key: resolved.get(key)
        for key in (
            "schema",
            "title_size_pt",
            "body_size_pt",
            "body_width_ratio_percent",
            "body_letter_spacing_percent",
            "line_spacing_percent",
            "question_spacing_pt",
            "match_source_style",
            "native_equations",
        )
    }
    for field in ("title_font", "body_font"):
        font = resolved.get(field)
        if not isinstance(font, dict):
            continue
        safe[field] = {
            key: font.get(key)
            for key in ("source", "key", "family_name")
            if font.get(key) is not None
        }
        asset = font.get("asset")
        if isinstance(asset, dict):
            safe[field]["asset"] = {
                key: asset.get(key)
                for key in ("id", "family_name", "original_name", "sha256")
                if asset.get(key) is not None
            }
    page_layout = resolved.get("page_layout")
    if isinstance(page_layout, dict):
        safe["page_layout"] = {
            key: page_layout.get(key)
            for key in (
                "mode",
                "page_width_mm",
                "page_height_mm",
                "margin_top_mm",
                "margin_bottom_mm",
                "margin_left_mm",
                "margin_right_mm",
                "column_count",
                "source_column_count",
                "column_gap_mm",
                "center_line",
                "center_line_style",
                "source_dimension_name",
            )
            if page_layout.get(key) is not None
        }
    return safe


def _safe_filename(value: str, *, default: str = "source") -> str:
    name = Path(value or default).name
    name = re.sub(r"[\\/:*?\"<>|]+", "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or default


def _doc_filename(source_name: str, suffix: str = ".doc") -> str:
    stem = Path(_safe_filename(source_name)).stem or "source"
    return f"{stem}_원본이관{suffix}"


def _mime_for_suffix(suffix: str) -> str:
    suffix = suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".bmp":
        return "image/bmp"
    if suffix == ".gif":
        return "image/gif"
    return "application/octet-stream"


def _image_page_size_points(data: bytes) -> tuple[float, float]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            width_px, height_px = image.size
            dpi_value = image.info.get("dpi")
            if (
                isinstance(dpi_value, tuple)
                and len(dpi_value) >= 2
                and 50 <= float(dpi_value[0]) <= 1200
                and 50 <= float(dpi_value[1]) <= 1200
            ):
                return (
                    width_px * 72.0 / float(dpi_value[0]),
                    height_px * 72.0 / float(dpi_value[1]),
                )
            if width_px > height_px:
                return 841.89, 595.28
            return 595.28, 841.89
    except Exception:
        return 595.28, 841.89


def _hwpx_raster_image(data: bytes, mime: str) -> tuple[str, bytes, int, int]:
    """Return a Hancom-safe PNG/JPEG and its pixel dimensions."""

    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        width_px, height_px = image.size
        if mime in {"image/jpeg", "image/png"}:
            return mime, data, int(width_px), int(height_px)
        converted = image.convert("RGB") if image.mode not in {"RGB", "L"} else image
        output = io.BytesIO()
        converted.save(output, format="PNG", optimize=True)
        return "image/png", output.getvalue(), int(width_px), int(height_px)


def _question_visuals_from_blocks(
    *,
    source_name: str,
    page_number: int,
    image_data: bytes,
    mime: str,
    text_blocks: Iterable[Any],
    coordinate_width: float,
    coordinate_height: float,
) -> list[QuestionVisual]:
    """Reuse the Matchup splitter to preserve problem-level figures and tables."""

    blocks = list(text_blocks)
    if not blocks or coordinate_width <= 0 or coordinate_height <= 0:
        return []
    try:
        from PIL import Image
        from academy.domain.tools.question_splitter import (
            TextBlock as SplitterTextBlock,
            split_questions,
        )

        splitter_blocks = [
            SplitterTextBlock(
                text=str(block.text),
                x0=float(block.x0),
                y0=float(block.y0),
                x1=float(block.x1),
                y1=float(block.y1),
            )
            for block in blocks
            if str(getattr(block, "text", "") or "").strip()
        ]
        regions = split_questions(
            splitter_blocks,
            page_width=float(coordinate_width),
            page_height=float(coordinate_height),
            page_index=page_number - 1,
        )
        if not regions:
            return []

        with Image.open(io.BytesIO(image_data)) as image:
            image = image.convert("RGB")
            scale_x = image.width / float(coordinate_width)
            scale_y = image.height / float(coordinate_height)
            visuals: list[QuestionVisual] = []
            for region in regions:
                raw_bbox = region.display_bbox or region.bbox
                x0 = max(0, min(image.width - 1, round(raw_bbox[0] * scale_x)))
                y0 = max(0, min(image.height - 1, round(raw_bbox[1] * scale_y)))
                x1 = max(x0 + 1, min(image.width, round(raw_bbox[2] * scale_x)))
                y1 = max(y0 + 1, min(image.height, round(raw_bbox[3] * scale_y)))
                if x1 - x0 < 8 or y1 - y0 < 8:
                    continue
                crop = image.crop((x0, y0, x1, y1))
                output = io.BytesIO()
                crop.save(output, format="JPEG", quality=90, optimize=True)
                visuals.append(QuestionVisual(
                    source_name=source_name,
                    page_number=page_number,
                    question_number=int(region.number),
                    mime="image/jpeg",
                    data=output.getvalue(),
                    width_px=crop.width,
                    height_px=crop.height,
                    semantic_flags=tuple(region.semantic_flags or ()),
                    bbox=tuple(float(value) for value in raw_bbox),
                ))
            return visuals
    except Exception:
        return []


def _visual_fragments_from_pdf_blocks(
    *,
    source_name: str,
    page_number: int,
    image_data: bytes,
    question_visuals: Iterable[QuestionVisual],
    visual_blocks: Iterable[Any],
    coordinate_width: float,
    coordinate_height: float,
) -> list[QuestionVisual]:
    """Crop only PDF image objects that belong to a visual-reference question."""

    blocks = list(visual_blocks)
    if not blocks:
        return []
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_data)) as image:
            image = image.convert("RGB")
            scale_x = image.width / coordinate_width
            scale_y = image.height / coordinate_height
            fragments: list[QuestionVisual] = []
            for question in question_visuals:
                if "visual_context" not in question.semantic_flags or len(question.bbox) != 4:
                    continue
                qx0, qy0, qx1, qy1 = question.bbox
                question_width = max(1.0, qx1 - qx0)
                candidates: list[tuple[float, float, float, float]] = []
                for block in blocks:
                    bx0 = float(block.x0)
                    by0 = float(block.y0)
                    bx1 = float(block.x1)
                    by1 = float(block.y1)
                    overlap_x = max(0.0, min(qx1, bx1) - max(qx0, bx0))
                    overlap_y = max(0.0, min(qy1, by1) - max(qy0, by0))
                    block_area = max(1.0, (bx1 - bx0) * (by1 - by0))
                    if overlap_x * overlap_y / block_area < 0.65:
                        continue
                    width_ratio = (bx1 - bx0) / question_width
                    if not 0.12 <= width_ratio <= 0.85:
                        continue
                    candidates.append((bx0, by0, bx1, by1))
                if not candidates:
                    continue
                padding_x = coordinate_width * 0.006
                padding_y = coordinate_height * 0.004
                x0 = max(0.0, min(candidate[0] for candidate in candidates) - padding_x)
                y0 = max(0.0, min(candidate[1] for candidate in candidates) - padding_y)
                x1 = min(coordinate_width, max(candidate[2] for candidate in candidates) + padding_x)
                y1 = min(coordinate_height, max(candidate[3] for candidate in candidates) + padding_y)
                pixel_bbox = (
                    max(0, round(x0 * scale_x)),
                    max(0, round(y0 * scale_y)),
                    min(image.width, round(x1 * scale_x)),
                    min(image.height, round(y1 * scale_y)),
                )
                if pixel_bbox[2] - pixel_bbox[0] < 8 or pixel_bbox[3] - pixel_bbox[1] < 8:
                    continue
                crop = image.crop(pixel_bbox)
                output = io.BytesIO()
                crop.save(output, format="JPEG", quality=94, optimize=True)
                fragments.append(QuestionVisual(
                    source_name=source_name,
                    page_number=page_number,
                    question_number=question.question_number,
                    mime="image/jpeg",
                    data=output.getvalue(),
                    width_px=crop.width,
                    height_px=crop.height,
                    semantic_flags=question.semantic_flags,
                    bbox=(x0, y0, x1, y1),
                    role="visual_fragment",
                ))
            return fragments
    except Exception:
        return []


def _detect_page_columns(
    image_data: bytes,
    *,
    mime: str,
    page_width: float,
    page_height: float,
    text_blocks: list[Any] | None = None,
    has_embedded_text: bool = False,
) -> tuple[int, str]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_data)) as image:
            if image.width < 200 or image.height < 200:
                return 1, "image_too_small"
    except Exception:
        return 1, "image_unreadable"
    from academy.adapters.tools.pymupdf_renderer import has_vertical_center_rule

    if has_vertical_center_rule(image_data):
        return 2, "pixel_center_rule"
    suffix = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }.get(mime, ".png")
    image_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as image_file:
            image_file.write(image_data)
            image_path = image_file.name
        from academy.domain.tools.paper_type import classify_paper_type

        result = classify_paper_type(
            text_blocks=text_blocks,
            image_path=image_path,
            page_width=page_width,
            page_height=page_height,
            has_embedded_text=has_embedded_text,
        )
        return (2 if result.is_dual_column else 1), str(result.paper_type.value)
    except Exception:
        return 1, "layout_fallback"
    finally:
        if image_path:
            try:
                os.unlink(image_path)
            except OSError:
                pass


def _effective_document_style(
    resolved: dict[str, Any] | None,
    documents: list[TransferDocument],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    style: dict[str, Any] = {
        "schema": "problem-studio-document-style/v1",
        "title_font": {
            "source": "builtin",
            "key": "hamchorom-dotum",
            "family_name": "함초롬돋움",
            "asset": None,
        },
        "body_font": {
            "source": "builtin",
            "key": "hamchorom-batang",
            "family_name": "함초롬바탕",
            "asset": None,
        },
        "title_size_pt": 20.0,
        "body_size_pt": 10.5,
        "body_width_ratio_percent": 100,
        "body_letter_spacing_percent": 0,
        "line_spacing_percent": 155,
        "question_spacing_pt": 10.0,
        "match_source_style": True,
        "native_equations": True,
    }
    if isinstance(resolved, dict):
        style.update(resolved)
    source_style_document = max(
        (
            doc
            for doc in documents
            if isinstance(doc.document_style_hint, dict)
            and doc.document_style_hint
        ),
        key=lambda doc: doc.text_chars,
        default=None,
    )
    source_hint = (
        dict(source_style_document.document_style_hint)
        if source_style_document is not None
        else {}
    )
    match_source_style = bool(style.get("match_source_style", True))
    if match_source_style and source_hint:
        source_font = str(source_hint.get("body_font_family") or "").strip()
        if source_font:
            selected_body_font = style.get("body_font")
            selected_family = (
                str(selected_body_font.get("family_name") or "").strip()
                if isinstance(selected_body_font, dict)
                else ""
            )
            # Keep the selected tenant font asset when it is the actual source font;
            # this preserves the authorized download snapshot in the result package.
            if selected_family.casefold() != source_font.casefold():
                style["body_font"] = {
                    "source": "source_document",
                    "family_name": source_font,
                    "asset": None,
                }
        for key in (
            "body_size_pt",
            "body_width_ratio_percent",
            "body_letter_spacing_percent",
            "line_spacing_percent",
        ):
            if source_hint.get(key) is not None:
                style[key] = source_hint[key]
    style["match_source_style"] = match_source_style
    requested_layout = style.get("page_layout")
    layout = dict(DEFAULT_PAGE_LAYOUT)
    if isinstance(requested_layout, dict):
        layout.update({
            key: requested_layout[key]
            for key in layout
            if key in requested_layout
        })

    dimension_source = next(
        (
            doc
            for doc in documents
            if doc.page_width_pt > 0 and doc.page_height_pt > 0
        ),
        None,
    )
    source_dimensions_preserved = False
    if dimension_source is not None:
        width_mm = dimension_source.page_width_pt * 25.4 / 72.0
        height_mm = dimension_source.page_height_pt * 25.4 / 72.0
        if not (90 <= width_mm <= 500 and 90 <= height_mm <= 500):
            width_mm, height_mm = 210.0, 297.0
        else:
            source_dimensions_preserved = True
    else:
        width_mm, height_mm = 210.0, 297.0

    detected_columns = [
        doc.detected_column_count
        for doc in documents
        if doc.detected_column_count in {1, 2}
    ]
    dual_count = sum(1 for count in detected_columns if count == 2)
    source_column_count = (
        2
        if dual_count >= max(1, (len(detected_columns) + 2) // 3)
        else 1
    )
    mode = str(layout.get("mode") or "source")
    if match_source_style and source_hint and mode == "source":
        source_margins = source_hint.get("margins_mm")
        if isinstance(source_margins, dict):
            for side in ("top", "bottom", "left", "right"):
                value = source_margins.get(side)
                if value is not None:
                    layout[f"margin_{side}_mm"] = float(value)
        for key in ("column_gap_mm", "center_line", "center_line_style"):
            if source_hint.get(key) is not None:
                layout[key] = source_hint[key]
    if mode == "korean_two_column":
        width_mm, height_mm = 210.0, 297.0
        column_count = 2
    elif mode == "single_column":
        column_count = 1
    else:
        column_count = source_column_count

    layout.update({
        "page_width_mm": round(width_mm, 2),
        "page_height_mm": round(height_mm, 2),
        "column_count": column_count,
        "source_column_count": source_column_count,
        "source_dimension_name": (
            dimension_source.source_name if dimension_source is not None else ""
        ),
        "source_dimensions_preserved": source_dimensions_preserved,
        "center_line": bool(layout.get("center_line", True) and column_count == 2),
    })
    style["page_layout"] = layout
    detected_layout = {
        "mode": mode,
        "page_width_mm": layout["page_width_mm"],
        "page_height_mm": layout["page_height_mm"],
        "column_count": column_count,
        "source_column_count": source_column_count,
        "center_line": layout["center_line"],
        "center_line_style": layout.get("center_line_style", "DASH"),
        "column_gap_mm": layout["column_gap_mm"],
        "source_dimension_name": layout["source_dimension_name"],
        "source_dimensions_preserved": source_dimensions_preserved,
        "source_style_matched": bool(match_source_style and source_hint),
        "source_style_name": (
            source_style_document.source_name
            if match_source_style and source_style_document is not None
            else ""
        ),
    }
    return (style or None), detected_layout


def _normalize_hwp_image_data(filename: str, data: bytes) -> tuple[str, bytes]:
    return normalize_hwp_image_data(filename, data)


def _data_url(data: bytes, mime: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _read_upload(uploaded: Any) -> tuple[str, bytes]:
    name = _safe_filename(str(getattr(uploaded, "name", "source") or "source"))
    size = int(getattr(uploaded, "size", 0) or 0)
    if size > TRANSFER_MAX_UPLOAD_BYTES:
        raise ValueError(f"{name} 파일 크기가 너무 큽니다.")
    data = uploaded.read()
    try:
        uploaded.seek(0)
    except Exception:
        pass
    if len(data) > TRANSFER_MAX_UPLOAD_BYTES:
        raise ValueError(f"{name} 파일 크기가 너무 큽니다.")
    return name, data


def _source_size_label(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _payload_meta(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "title": str(payload.get("title") or "문제 제작 원본 이관"),
        "class_name": str(payload.get("class_name") or ""),
        "subject": str(payload.get("subject") or ""),
        "template_name": str(payload.get("template_name") or "매치업 기존 양식"),
        "note_policy": str(payload.get("note_policy") or ""),
    }


def _source_kind(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return {
        ".pdf": "PDF",
        ".hwp": "HWP",
        ".hwpx": "HWPX",
        ".docx": "DOCX",
        ".doc": "DOC",
        ".png": "이미지",
        ".jpg": "이미지",
        ".jpeg": "이미지",
        ".webp": "이미지",
        ".bmp": "이미지",
        ".zip": "ZIP",
    }.get(suffix, "기타")


def _office_doc_shell(title: str, body: str, *, extra_style: str = "") -> str:
    return f"""<!doctype html>
<html lang="ko" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
  <meta charset="utf-8" />
  <title>{_escape(title)}</title>
  <style>
    @page {{ size: A4; margin: 14mm 13mm 16mm; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: #111827;
      font-family: "Malgun Gothic", "맑은 고딕", "Batang", "바탕", serif;
      font-size: 10.5pt;
      line-height: 1.55;
    }}
    h1 {{
      margin: 0 0 8mm;
      padding-bottom: 4mm;
      border-bottom: 1.2pt solid #111827;
      font-size: 18pt;
      line-height: 1.25;
    }}
    .transfer-meta {{
      margin: 0 0 8mm;
      padding: 3mm 4mm;
      border-left: 3pt solid #2563eb;
      background: #eff6ff;
      color: #1f2937;
      font-size: 9.5pt;
    }}
    .source-page {{
      page-break-after: always;
      break-after: page;
      margin: 0 0 8mm;
    }}
    .source-page:last-child {{
      page-break-after: auto;
      break-after: auto;
    }}
    .source-page img, .source-image {{
      display: block;
      width: 100%;
      height: auto;
      page-break-inside: avoid;
    }}
    .source-text {{
      white-space: pre-wrap;
      word-break: keep-all;
      overflow-wrap: anywhere;
    }}
    .warning {{
      margin: 6mm 0;
      padding: 3mm 4mm;
      border: 1pt solid #f59e0b;
      background: #fffbeb;
      color: #92400e;
      font-weight: 700;
    }}
    {extra_style}
  </style>
</head>
<body>
{body}
</body>
</html>"""


def _meta_block(title: str, source_name: str, detail: str) -> str:
    return f"""
  <h1>{_escape(title)}</h1>
  <div class="transfer-meta">
    <strong>원본 파일</strong> {_escape(source_name)}<br />
    <strong>이관 방식</strong> {_escape(detail)}<br />
    <strong>생성 시각</strong> {_escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}
  </div>
"""


def _ocr_status_from_units(completed: int, pending: int) -> str:
    if completed and pending:
        return "partial"
    if completed:
        return "extracted"
    if pending:
        return "queued"
    return "not_applicable"


def _ocr_note_html(result: OcrResult) -> str:
    label = {
        "skipped_limit": "자동 OCR 처리 한도를 초과해 후보로 남겼습니다.",
        "unavailable": "자동 OCR 엔진을 사용할 수 없어 후보로 남겼습니다.",
        "disabled": "자동 OCR이 비활성화되어 후보로 남겼습니다.",
        "empty": "자동 OCR에서 편집 가능한 텍스트를 찾지 못했습니다.",
        "error": "자동 OCR 처리 중 오류가 발생해 후보로 남겼습니다.",
    }.get(result.status, "OCR 후보로 남겼습니다.")
    warning = f" {_escape(result.warning)}" if result.warning else ""
    return f'<p class="ocr-note"><strong>OCR 대기</strong> {_escape(label)}{warning}</p>'


def _image_transfer_doc(name: str, data: bytes, *, ocr_context: TransferOcrContext) -> TransferDocument:
    mime = _mime_for_suffix(Path(name).suffix)
    page_width_pt, page_height_pt = _image_page_size_points(data)
    source_mime, source_data, width_px, height_px = _hwpx_raster_image(data, mime)
    detected_columns, layout_source = _detect_page_columns(
        data,
        mime=mime,
        page_width=page_width_pt,
        page_height=page_height_pt,
    )
    ocr_result = ocr_context.extract(data, mime=mime)
    ocr_text = ocr_result.text if ocr_result.status == "extracted" else ""
    question_visuals = _question_visuals_from_blocks(
        source_name=name,
        page_number=1,
        image_data=source_data,
        mime=source_mime,
        text_blocks=ocr_result.blocks,
        coordinate_width=float(width_px),
        coordinate_height=float(height_px),
    )
    ocr_html = ""
    if ocr_text:
        ocr_html = f"""
  <h2>자동 OCR 텍스트</h2>
  <div class="source-text">{_escape(ocr_text)}</div>
"""
    else:
        ocr_html = _ocr_note_html(ocr_result)
    ocr_completed = 1 if ocr_text else 0
    ocr_pending = 0 if ocr_text else 1
    body = (
        _meta_block("원본 이미지 이관", name, "이미지 원본 보존 + 자동 OCR 시도")
        + f'<img class="source-image" src="{_data_url(data, mime)}" alt="{_escape(name)}" />'
        + ocr_html
    )
    return TransferDocument(
        filename=_doc_filename(name),
        html=_office_doc_shell(
            name,
            body,
            extra_style="""
    h2 { margin: 9mm 0 3mm; font-size: 13pt; }
    .ocr-note { margin: 5mm 0; padding: 3mm 4mm; border: 1pt solid #f59e0b; background: #fffbeb; color: #92400e; }
""",
        ),
        source_name=name,
        kind=_source_kind(name),
        text_chars=len(ocr_text),
        image_count=1,
        page_start=1,
        page_end=1,
        plain_text=ocr_text,
        ocr_text_chars=len(ocr_text),
        ocr_completed_units=ocr_completed,
        ocr_pending_units=ocr_pending,
        ocr_status=_ocr_status_from_units(ocr_completed, ocr_pending),
        ocr_engine=ocr_result.engine if ocr_text else "",
        ocr_warning=None if ocr_text else (ocr_result.warning or ocr_result.status),
        page_width_pt=page_width_pt,
        page_height_pt=page_height_pt,
        detected_column_count=detected_columns,
        layout_source=layout_source,
        source_pages=[
            SourcePageVisual(
                source_name=name,
                page_number=1,
                mime=source_mime,
                data=source_data,
                width_px=width_px,
                height_px=height_px,
                page_width_pt=page_width_pt,
                page_height_pt=page_height_pt,
            )
        ],
        question_visuals=question_visuals,
    )


def _pdf_transfer_docs(name: str, data: bytes, *, ocr_context: TransferOcrContext) -> list[TransferDocument]:
    try:
        from academy.adapters.tools.pymupdf_renderer import PdfBytesDocument
    except Exception as exc:  # pragma: no cover - dependency is present in api image
        raise ValueError("PDF 렌더링 모듈을 사용할 수 없습니다.") from exc

    documents: list[TransferDocument] = []
    with PdfBytesDocument(data) as pdf:
        page_count = pdf.page_count()
        for start in range(0, page_count, TRANSFER_PDF_PAGES_PER_DOC):
            end = min(start + TRANSFER_PDF_PAGES_PER_DOC, page_count)
            page_width_pt, page_height_pt = pdf.page_size(start)
            detected_page_columns: list[int] = []
            detected_layout_sources: list[str] = []
            part_text_chunks: list[str] = []
            part_ocr_completed = 0
            part_ocr_pending = 0
            part_ocr_engine = ""
            part_ocr_warning = ""
            part_ocr_text_chars = 0
            part_source_pages: list[SourcePageVisual] = []
            part_question_visuals: list[QuestionVisual] = []
            page_html: list[str] = [
                _meta_block(
                    "PDF 원본 페이지 이관",
                    name,
                    f"페이지 이미지 보존 + 텍스트 레이어 분석 + 자동 OCR 제한 처리 · {start + 1}-{end}쪽 / 총 {page_count}쪽",
                )
            ]
            for index in range(start, end):
                page_text = normalize_space(pdf.extract_page_text(index))
                if page_text:
                    part_text_chunks.append(f"[{index + 1}쪽]\n{page_text}")
                mime, image_bytes = pdf.render_page_bytes(index, zoom=TRANSFER_PDF_RENDER_ZOOM, jpg_quality=82)
                current_width_pt, current_height_pt = pdf.page_size(index)
                text_blocks = pdf.extract_page_text_blocks(index) if page_text else []
                page_visual_blocks = (
                    pdf.extract_page_visual_blocks(index)
                    if page_text
                    else []
                )
                page_columns, layout_source = _detect_page_columns(
                    image_bytes,
                    mime=mime,
                    page_width=current_width_pt,
                    page_height=current_height_pt,
                    text_blocks=text_blocks,
                    has_embedded_text=bool(page_text),
                )
                detected_page_columns.append(page_columns)
                detected_layout_sources.append(layout_source)
                source_mime, source_data, width_px, height_px = _hwpx_raster_image(
                    image_bytes,
                    mime,
                )
                part_source_pages.append(SourcePageVisual(
                    source_name=name,
                    page_number=index + 1,
                    mime=source_mime,
                    data=source_data,
                    width_px=width_px,
                    height_px=height_px,
                    page_width_pt=current_width_pt,
                    page_height_pt=current_height_pt,
                ))
                ocr_page_html = ""
                visual_blocks: Iterable[Any] = text_blocks
                coordinate_width = current_width_pt
                coordinate_height = current_height_pt
                if not page_text:
                    ocr_result = ocr_context.extract(image_bytes, mime=mime)
                    if ocr_result.status == "extracted" and ocr_result.text:
                        part_ocr_completed += 1
                        part_ocr_engine = ocr_result.engine
                        part_ocr_text_chars += len(ocr_result.text)
                        part_text_chunks.append(f"[{index + 1}쪽 OCR]\n{ocr_result.text}")
                        ocr_page_html = f'<div class="source-text ocr-text"><strong>자동 OCR 텍스트</strong><br />{_escape(ocr_result.text)}</div>'
                        visual_blocks = ocr_result.blocks
                        coordinate_width = float(width_px)
                        coordinate_height = float(height_px)
                    else:
                        part_ocr_pending += 1
                        part_ocr_warning = part_ocr_warning or ocr_result.warning or ocr_result.status
                        ocr_page_html = _ocr_note_html(ocr_result)
                page_question_visuals = _question_visuals_from_blocks(
                    source_name=name,
                    page_number=index + 1,
                    image_data=source_data,
                    mime=source_mime,
                    text_blocks=visual_blocks,
                    coordinate_width=coordinate_width,
                    coordinate_height=coordinate_height,
                )
                part_question_visuals.extend(page_question_visuals)
                part_question_visuals.extend(_visual_fragments_from_pdf_blocks(
                    source_name=name,
                    page_number=index + 1,
                    image_data=source_data,
                    question_visuals=page_question_visuals,
                    visual_blocks=page_visual_blocks,
                    coordinate_width=current_width_pt,
                    coordinate_height=current_height_pt,
                ))
                page_no = index + 1
                page_html.append(
                    f'<section class="source-page">'
                    f'<p><strong>{page_no}쪽</strong></p>'
                    f'<img src="{_data_url(image_bytes, mime)}" alt="{_escape(name)} {page_no}쪽" />'
                    f'{ocr_page_html}'
                    f'</section>'
                )
            part_label = f"_part{(start // TRANSFER_PDF_PAGES_PER_DOC) + 1:02d}" if page_count > TRANSFER_PDF_PAGES_PER_DOC else ""
            filename = _doc_filename(f"{Path(name).stem}{part_label}.pdf")
            plain_text = normalize_space("\n\n".join(part_text_chunks))
            dual_page_count = sum(1 for count in detected_page_columns if count == 2)
            detected_column_count = (
                2
                if dual_page_count >= max(1, (len(detected_page_columns) + 2) // 3)
                else 1
            )
            documents.append(TransferDocument(
                filename=filename,
                html=_office_doc_shell(
                    f"{name} {start + 1}-{end}쪽",
                    "\n".join(page_html),
                    extra_style="""
    .ocr-note { margin: 4mm 0; padding: 3mm 4mm; border: 1pt solid #f59e0b; background: #fffbeb; color: #92400e; }
    .ocr-text { margin: 4mm 0 0; padding: 3mm 4mm; border-left: 3pt solid #0f766e; background: #f0fdfa; }
""",
                ),
                source_name=name,
                kind="PDF",
                page_count=end - start,
                page_start=start + 1,
                page_end=end,
                image_count=end - start,
                text_chars=len(plain_text),
                plain_text=plain_text,
                ocr_text_chars=part_ocr_text_chars,
                ocr_completed_units=part_ocr_completed,
                ocr_pending_units=part_ocr_pending,
                ocr_status=_ocr_status_from_units(part_ocr_completed, part_ocr_pending),
                ocr_engine=part_ocr_engine,
                ocr_warning=part_ocr_warning or None,
                page_width_pt=page_width_pt,
                page_height_pt=page_height_pt,
                detected_column_count=detected_column_count,
                layout_source=", ".join(sorted(set(detected_layout_sources))),
                source_pages=part_source_pages,
                question_visuals=part_question_visuals,
            ))
    return documents


def _simple_text_transfer_doc(name: str, text: str, detail: str, warning: str | None = None) -> TransferDocument:
    warning_html = f'<div class="warning">{_escape(warning)}</div>' if warning else ""
    body = (
        _meta_block("문서 본문 이관", name, detail)
        + warning_html
        + f'<div class="source-text">{_escape(text or "추출된 본문이 없습니다.")}</div>'
    )
    return TransferDocument(
        filename=_doc_filename(name),
        html=_office_doc_shell(name, body),
        source_name=name,
        kind=_source_kind(name),
        text_chars=len(text or ""),
        warning=warning,
        plain_text=text or "",
    )


def _extract_hwp_text_and_images(data: bytes, *, include_images: bool = True) -> tuple[str, list[tuple[str, str, bytes]]]:
    return extract_hwp_text_and_images(data, include_images=include_images)


def extract_hwp_text(data: bytes) -> str:
    return extract_hwp_text_only(data)


def _fast_hwp_transfer_doc(name: str, data: bytes) -> TransferDocument:
    text, images = _extract_hwp_text_and_images(data)
    image_html = []
    for index, (image_name, mime, image_data) in enumerate(images, start=1):
        image_html.append(
            f'<figure class="hwp-image">'
            f'<img src="{_data_url(image_data, mime)}" alt="{_escape(image_name)}" />'
            f'<figcaption>{index}. {_escape(image_name)}</figcaption>'
            f'</figure>'
        )
    body = (
        _meta_block("HWP 원본 이관", name, "HWP 본문 텍스트 + 임베디드 이미지 전체 추출")
        + '<h2>본문 텍스트</h2>'
        + f'<div class="source-text">{_escape(text or "추출된 본문 텍스트가 없습니다.")}</div>'
        + '<h2>그림/도표 이미지</h2>'
        + (
            f'<div class="hwp-gallery">{"".join(image_html)}</div>'
            if image_html
            else '<p class="warning">추출된 이미지가 없습니다.</p>'
        )
    )
    return TransferDocument(
        filename=_doc_filename(name),
        html=_office_doc_shell(
            name,
            body,
            extra_style=f"""
    h2 {{ margin: 10mm 0 4mm; font-size: 13pt; }}
    .hwp-gallery {{ display: grid; grid-template-columns: repeat({TRANSFER_HWP_GALLERY_IMAGES_PER_ROW}, minmax(0, 1fr)); gap: 5mm; }}
    .hwp-image {{ margin: 0 0 5mm; page-break-inside: avoid; }}
    .hwp-image img {{ display: block; max-width: 100%; height: auto; }}
    .hwp-image figcaption {{ margin-top: 1.5mm; color: #6b7280; font-size: 8.5pt; text-align: right; }}
""",
        ),
        source_name=name,
        kind="HWP",
        text_chars=len(text),
        image_count=len(images),
        plain_text=text,
    )


def _extract_hwpx_text(data: bytes) -> str:
    return extract_hwpx_text(data)


def _extract_docx_text(data: bytes) -> str:
    return extract_docx_text(data)


def _docs_from_named_bytes(
    name: str,
    data: bytes,
    *,
    ocr_context: TransferOcrContext,
) -> tuple[list[TransferDocument], list[str]]:
    suffix = Path(name).suffix.lower()
    warnings: list[str] = []
    try:
        if suffix == ".pdf":
            return _pdf_transfer_docs(name, data, ocr_context=ocr_context), warnings
        if suffix == ".hwp":
            return [_fast_hwp_transfer_doc(name, data)], warnings
        if suffix == ".hwpx":
            text = _extract_hwpx_text(data)
            profile = extract_hwpx_document_profile(data)
            extracted_visuals = extract_hwpx_question_images(data)
            document = _simple_text_transfer_doc(name, text, "HWPX 본문 텍스트·서식 이관")
            document.page_width_pt = float(profile.get("page_width_pt") or 0)
            document.page_height_pt = float(profile.get("page_height_pt") or 0)
            document.detected_column_count = int(profile.get("column_count") or 0)
            document.layout_source = "hwpx_native_profile"
            document.page_count = int(profile.get("estimated_min_pages") or 0)
            document.document_style_hint = profile
            document.image_count = len(extracted_visuals)
            document.question_visuals = [
                QuestionVisual(
                    source_name=name,
                    page_number=0,
                    question_number=int(visual["question_number"]),
                    mime=str(visual["mime"]),
                    data=bytes(visual["data"]),
                    width_px=int(visual["width_px"]),
                    height_px=int(visual["height_px"]),
                    semantic_flags=("visual_context", "hwpx_embedded"),
                    role="visual_fragment",
                )
                for visual in extracted_visuals
            ]
            return [document], warnings
        if suffix == ".docx":
            text = _extract_docx_text(data)
            return [_simple_text_transfer_doc(name, text, "DOCX 본문 텍스트 이관")], warnings
        if suffix == ".doc":
            warning = "DOC 바이너리는 직접 렌더링하지 못해 원본 등록 리포트만 생성했습니다. DOCX/PDF로 저장하면 본문 이관이 가능합니다."
            return [_simple_text_transfer_doc(name, "", "DOC 원본 등록", warning)], [f"{name}: {warning}"]
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            return [_image_transfer_doc(name, data, ocr_context=ocr_context)], warnings
    except Exception as exc:
        warning = f"{name}: 원본 이관 중 오류가 발생했습니다. ({exc})"
        return [_simple_text_transfer_doc(name, "", "오류 리포트", warning)], [warning]
    warning = f"{name}: 지원하지 않는 파일 형식입니다."
    return [_simple_text_transfer_doc(name, "", "지원하지 않는 파일", warning)], [warning]


def _expand_zip(name: str, data: bytes) -> tuple[list[tuple[str, bytes]], list[str]]:
    warnings: list[str] = []
    members: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        safe_zip_members(
            zf,
            max_members=TRANSFER_MAX_ZIP_MEMBERS,
            max_uncompressed_bytes=TRANSFER_MAX_ZIP_UNCOMPRESSED_BYTES,
        )
        for info in infos:
            suffix = Path(info.filename).suffix.lower()
            if suffix not in SUPPORTED_TRANSFER_SUFFIXES:
                continue
            safe_member_name = _safe_filename(Path(info.filename).name)
            if not safe_member_name:
                continue
            members.append((safe_member_name, zf.read(info)))
        if not members:
            warnings.append(f"{name}: ZIP 안에서 지원하는 문서 파일을 찾지 못했습니다.")
    return members, warnings


def _review_focus(doc: TransferDocument) -> str:
    if doc.warning:
        return "경고 내용을 먼저 확인하고 원본 파일과 대조하세요."
    if doc.ocr_pending_units and doc.ocr_completed_units:
        return "자동 OCR 텍스트를 원본 이미지와 대조하고 남은 OCR 대기 범위를 확인하세요."
    if doc.ocr_pending_units:
        return "시각 보존 상태를 확인하고 OCR 후보로 남은 범위를 후속 처리하세요."
    if doc.ocr_completed_units:
        return "자동 OCR 텍스트의 오인식, 수식, 표, 선택지 누락을 원본과 대조하세요."
    if doc.kind == "PDF":
        return "첫 쪽, 중간 쪽, 마지막 쪽을 원본과 대조하고 잘림/회전/누락을 확인하세요."
    if doc.kind == "HWP":
        return "본문 텍스트 순서, 그림/도표 이미지, 표·수식 위치를 원본과 대조하세요."
    if doc.kind in {"HWPX", "DOCX"}:
        return "본문 텍스트가 모두 들어왔는지 확인하고 표·그림은 원본과 대조하세요."
    if doc.kind == "이미지":
        return "이미지 해상도와 잘림을 확인하세요. 텍스트 편집은 OCR 보강 전까지 수동 작업입니다."
    if doc.kind == "DOC":
        return "DOC 바이너리는 본문 추출이 제한됩니다. DOCX/PDF로 저장한 뒤 다시 이관하는 편이 안전합니다."
    return "원본과 산출물을 직접 대조하세요."


def _review_status(doc: TransferDocument) -> str:
    if doc.warning:
        return "확인 필요"
    if doc.ocr_pending_units and doc.text_chars > 0:
        return "OCR 일부 대기"
    if doc.ocr_pending_units:
        return "OCR 대기"
    if doc.ocr_completed_units:
        return "OCR 검수"
    if doc.kind in {"PDF", "이미지"} and doc.text_chars == 0:
        return "시각 대조"
    return "검수 대기"


def _quality_label(value: str) -> str:
    return {
        "structured_review_ready": "문제 단위 검수 가능",
        "mixed_review_ocr_recommended": "혼합 자료 · OCR 권장",
        "visual_only_ocr_required": "시각 이관 완료 · OCR 필요",
        "needs_attention": "확인 필요",
        "manual_review_required": "수동 검수 필요",
    }.get(value, value or "수동 검수 필요")


def _build_review_checklist_html(
    meta: dict[str, str],
    input_files: list[dict[str, Any]],
    documents: list[TransferDocument],
    warnings: list[str],
    structure: TransferStructure,
) -> str:
    source_rows = "\n".join(
        f"<tr><td>{index}</td><td>{_escape(item['name'])}</td><td>{_escape(item['kind'])}</td><td>{_escape(item['sizeLabel'])}</td></tr>"
        for index, item in enumerate(input_files, start=1)
    ) or '<tr><td colspan="4">등록된 원본 파일 없음</td></tr>'
    review_rows = "\n".join(
        f"<tr>"
        f"<td>{index}</td>"
        f"<td>{_escape(doc.filename)}</td>"
        f"<td>{_escape(doc.kind)}</td>"
        f"<td>{doc.page_count}</td>"
        f"<td>{doc.image_count}</td>"
        f"<td>{_escape(_review_status(doc))}</td>"
        f"<td>{_escape(_review_focus(doc))}</td>"
        f"</tr>"
        for index, doc in enumerate(documents, start=1)
    )
    warning_items = "\n".join(f"<li>{_escape(w)}</li>" for w in warnings) or "<li>경고 없음</li>"
    body = f"""
  <h1>{_escape(meta['title'])} 검수 체크리스트</h1>
  <div class="transfer-meta">
    <strong>반명</strong> {_escape(meta['class_name'] or "-")}<br />
    <strong>과목</strong> {_escape(meta['subject'] or "-")}<br />
    <strong>기준 양식</strong> {_escape(meta['template_name'])}<br />
    <strong>생성 시각</strong> {_escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}
  </div>
  <h2>1. 실사용 검수 순서</h2>
  <ol>
    <li><strong>01_자체양식_문제검수본.doc</strong>에서 자동 분리된 문제/개념 블록을 먼저 확인합니다.</li>
    <li><strong>03_자체양식_문제검수본.hwpx</strong>에서 원본 페이지 크기·단 구성·중앙선과 전사 내용을 확인합니다.</li>
    <li><strong>04_선생님문체_해설검수본.hwpx</strong>에서 정답 근거와 선생님 문체 해설을 확인합니다.</li>
    <li><strong>05_원본충실_레이아웃대조본.hwpx</strong>에서 스캔의 표·박스·그림·상대 배치가 빠짐없이 보존됐는지 확인합니다.</li>
    <li><strong>00_변환리포트.html</strong>에서 경고와 문서 수를 먼저 확인합니다.</li>
    <li><strong>02_OCR_연결후보.csv</strong>에서 자동 OCR 후에도 남은 스캔/이미지 원본을 별도 처리 목록으로 확인합니다.</li>
    <li>각 산출물 `.doc`을 한글 또는 Word에서 열고 편집 가능 여부를 확인합니다.</li>
    <li>원본의 첫 쪽, 중간 쪽, 마지막 쪽을 산출물과 대조합니다.</li>
    <li>그림, 표, 수식, 선택지, 정답/해설이 누락되거나 섞이지 않았는지 표시합니다.</li>
    <li>수업 배포 전 정답과 해설은 선생님이 직접 확정합니다.</li>
  </ol>
  <h2>2. 합격 기준</h2>
  <table>
    <tbody>
      <tr><th>구조화</th><td>자동 분리 항목 {structure.structured_item_count}개, 문제 후보 {structure.structured_problem_count}개. 상태: {_escape(_quality_label(structure.quality_level))}</td></tr>
      <tr><th>열림</th><td>한글/Word에서 모든 `.doc` 파일이 열리고, 한글에서 문제·해설·원본충실 HWPX 3개가 모두 열립니다.</td></tr>
      <tr><th>누락</th><td>원본 페이지 또는 주요 그림/표가 빠지지 않았습니다.</td></tr>
      <tr><th>수정성</th><td>선생님이 수업용으로 직접 고칠 수 있는 상태입니다.</td></tr>
      <tr><th>OCR</th><td>자동 OCR 처리 {structure.ocr_completed_unit_count}단위, 남은 OCR 후보 {structure.ocr_candidate_count}개/{structure.ocr_pending_unit_count}단위.</td></tr>
      <tr><th>Beta</th><td>재작성 후보는 참고용이며 정답/표현은 확정 전 검수합니다.</td></tr>
    </tbody>
  </table>
  <h2>3. 자동 검수 액션</h2>
  <ul>{"".join(f"<li>{_escape(action)}</li>" for action in structure.review_actions)}</ul>
  <h2>4. 원본 파일</h2>
  <table>
    <thead><tr><th>#</th><th>파일명</th><th>유형</th><th>크기</th></tr></thead>
    <tbody>{source_rows}</tbody>
  </table>
  <h2>5. 산출물별 확인 포인트</h2>
  <table>
    <thead><tr><th>#</th><th>산출물</th><th>유형</th><th>쪽수</th><th>이미지</th><th>상태</th><th>확인 포인트</th></tr></thead>
    <tbody>{review_rows}</tbody>
  </table>
  <h2>6. 경고</h2>
  <ul>{warning_items}</ul>
  <h2>7. 선생님 피드백 기록</h2>
  <table>
    <thead><tr><th>파일</th><th>위치</th><th>문제 유형</th><th>수정 필요 내용</th><th>처리</th></tr></thead>
    <tbody>
      <tr><td>&nbsp;</td><td></td><td>누락 / 깨짐 / 순서 / 정답 / 기타</td><td></td><td></td></tr>
      <tr><td>&nbsp;</td><td></td><td>누락 / 깨짐 / 순서 / 정답 / 기타</td><td></td><td></td></tr>
      <tr><td>&nbsp;</td><td></td><td>누락 / 깨짐 / 순서 / 정답 / 기타</td><td></td><td></td></tr>
    </tbody>
  </table>
"""
    return _office_doc_shell(
        f"{meta['title']} 검수 체크리스트",
        body,
        extra_style="""
    h2 { margin: 9mm 0 3mm; font-size: 13pt; }
    ol { margin: 0 0 5mm 6mm; padding-left: 5mm; }
    li { margin: 1.5mm 0; }
    table { width: 100%; border-collapse: collapse; margin: 4mm 0 7mm; }
    th, td { border: 0.5pt solid #cbd5e1; padding: 2mm; vertical-align: top; font-size: 9pt; }
    th { background: #eef2ff; color: #111827; }
""",
    )


def _source_page_payloads(documents: Iterable[TransferDocument]) -> list[dict[str, Any]]:
    return [
        {
            "source_name": page.source_name,
            "page_number": page.page_number,
            "mime": page.mime,
            "data": page.data,
            "width_px": page.width_px,
            "height_px": page.height_px,
            "page_width_pt": page.page_width_pt,
            "page_height_pt": page.page_height_pt,
        }
        for document in documents
        for page in document.source_pages
    ]


def _question_visual_map(
    documents: Iterable[TransferDocument],
) -> dict[int, dict[str, Any]]:
    selected: dict[int, QuestionVisual] = {}
    for document in documents:
        for visual in document.question_visuals:
            if visual.role != "visual_fragment":
                continue
            current = selected.get(visual.question_number)
            if current is None or (
                "visual_context" in visual.semantic_flags
                and "visual_context" not in current.semantic_flags
            ):
                selected[visual.question_number] = visual
    return {
        number: {
            "mime": visual.mime,
            "data": visual.data,
            "width_px": visual.width_px,
            "height_px": visual.height_px,
            "source_name": visual.source_name,
            "page_number": visual.page_number,
            "semantic_flags": list(visual.semantic_flags),
        }
        for number, visual in selected.items()
        if "visual_context" in visual.semantic_flags
    }


def _reconstruction_quality(
    documents: Iterable[TransferDocument],
    structure: TransferStructure,
) -> dict[str, Any]:
    docs = list(documents)
    expected_source_pages = sum(
        max(int(doc.page_count or 0), int(doc.image_count or 0))
        for doc in docs
        if doc.kind in {"PDF", "이미지"}
    )
    preserved_source_pages = sum(len(doc.source_pages) for doc in docs)
    source_page_coverage = (
        min(1.0, preserved_source_pages / expected_source_pages)
        if expected_source_pages
        else 1.0
    )
    problem_numbers = {
        item.number for item in structure.items if item.item_type == "problem"
    }
    crop_numbers = {
        visual.question_number
        for doc in docs
        for visual in doc.question_visuals
        if visual.role == "question_crop"
    }
    question_crop_coverage = (
        len(problem_numbers & crop_numbers) / len(problem_numbers)
        if problem_numbers
        else 0.0
    )
    embedded_visual_numbers = set(_question_visual_map(docs))
    visual_context_numbers = {
        visual.question_number
        for doc in docs
        for visual in doc.question_visuals
        if visual.role == "question_crop"
        and "visual_context" in visual.semantic_flags
    }
    visual_fragment_coverage = (
        len(visual_context_numbers & embedded_visual_numbers)
        / len(visual_context_numbers)
        if visual_context_numbers
        else 1.0
    )
    if (
        structure.quality_level == "structured_review_ready"
        and structure.ocr_pending_unit_count == 0
        and source_page_coverage == 1.0
        and (not expected_source_pages or question_crop_coverage >= 0.9)
        and visual_fragment_coverage == 1.0
    ):
        gate = "benchmark_candidate"
    elif problem_numbers and source_page_coverage == 1.0:
        gate = "hybrid_review_required"
    else:
        gate = "source_review_required"
    return {
        "schema": "problem-studio-reconstruction-quality/v1",
        "gate": gate,
        "benchmark_target": "editable_text_equations_plus_pixel_faithful_visuals",
        "source_page_count": expected_source_pages,
        "source_page_preserved_count": preserved_source_pages,
        "source_page_coverage": round(source_page_coverage, 4),
        "question_crop_count": sum(
            1
            for doc in docs
            for visual in doc.question_visuals
            if visual.role == "question_crop"
        ),
        "question_crop_coverage": round(question_crop_coverage, 4),
        "embedded_visual_question_count": len(embedded_visual_numbers),
        "visual_fragment_coverage": round(visual_fragment_coverage, 4),
        "native_equations": True,
        "teacher_review_required": True,
    }


def _build_manifest_json(
    meta: dict[str, str],
    input_files: list[dict[str, Any]],
    documents: list[TransferDocument],
    warnings: list[str],
    structure: TransferStructure,
    document_style: dict[str, Any] | None = None,
    explanation_count: int = 0,
    detected_layout: dict[str, Any] | None = None,
    reconstruction_quality: dict[str, Any] | None = None,
) -> str:
    manifest = {
        "schema": "problem-studio-transfer-manifest/v4",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "title": meta["title"],
        "class_name": meta["class_name"],
        "subject": meta["subject"],
        "template_name": meta["template_name"],
        "document_count": len(documents),
        "warning_count": len(warnings),
        "image_count": sum(doc.image_count for doc in documents),
        "page_count": sum(doc.page_count for doc in documents),
        "text_chars": sum(doc.text_chars for doc in documents),
        "structured_item_count": structure.structured_item_count,
        "structured_problem_count": structure.structured_problem_count,
        "ocr_candidate_count": structure.ocr_candidate_count,
        "ocr_completed_unit_count": structure.ocr_completed_unit_count,
        "ocr_pending_unit_count": structure.ocr_pending_unit_count,
        "quality_level": structure.quality_level,
        "generated_explanation_count": explanation_count,
        "detected_layout": detected_layout or {},
        "reconstruction_quality": reconstruction_quality or {},
        "document_style": _safe_document_style(document_style),
        "input_files": input_files,
        "documents": [
            {
                "filename": doc.filename,
                "source_name": doc.source_name,
                "kind": doc.kind,
                "page_count": doc.page_count,
                "image_count": doc.image_count,
                "text_chars": doc.text_chars,
                "ocr_text_chars": doc.ocr_text_chars,
                "ocr_completed_units": doc.ocr_completed_units,
                "ocr_pending_units": doc.ocr_pending_units,
                "ocr_status": doc.ocr_status,
                "ocr_engine": doc.ocr_engine,
                "ocr_warning": doc.ocr_warning,
                "page_width_pt": round(doc.page_width_pt, 2),
                "page_height_pt": round(doc.page_height_pt, 2),
                "detected_column_count": doc.detected_column_count,
                "layout_source": doc.layout_source,
                "status": _review_status(doc),
                "review_focus": _review_focus(doc),
                "warning": doc.warning,
            }
            for doc in documents
        ],
        "warnings": warnings,
        "structure": structure.to_manifest(),
        "template_outputs": [
            {
                "filename": "01_자체양식_문제검수본.doc",
                "type": "academy_review_workbook",
                "status": _quality_label(structure.quality_level),
            },
            {
                "filename": "03_자체양식_문제검수본.hwpx",
                "type": "academy_review_workbook_hwpx",
                "status": "원본 규격 기반 한글 HWPX 문제지",
            },
            {
                "filename": "04_선생님문체_해설검수본.hwpx",
                "type": "academy_solution_workbook_hwpx",
                "status": f"AI 해설 {explanation_count}개 · 선생님 최종 검수 필요",
            },
            {
                "filename": "02_OCR_연결후보.csv",
                "type": "ocr_work_queue",
                "status": "남은 OCR 후보 " + str(structure.ocr_candidate_count) + "개",
            },
            {
                "filename": "05_원본충실_레이아웃대조본.hwpx",
                "type": "source_fidelity_reference_hwpx",
                "status": (
                    f"원본 페이지 {int((reconstruction_quality or {}).get('source_page_preserved_count') or 0)}쪽 "
                    "픽셀 보존"
                ),
            },
            {
                "filename": "06_업로드원본_그대로/",
                "type": "byte_identical_source_archive",
                "status": "업로드 원본 바이트 그대로 보존",
            },
        ],
        "review_contract": {
            "base_workflow": "source_transfer_teacher_review",
            "teacher_must_verify_answers": True,
            "ocr_auto_enabled": problem_studio_ocr_enabled(),
            "ocr_auto_max_units": problem_studio_ocr_max_units(),
            "ocr_required_for_scanned_text": structure.ocr_candidate_count > 0,
            "native_hwp_output": False,
            "native_hwpx_output": True,
            "native_hwpx_equations": True,
            "auto_explanations": explanation_count > 0,
            "generated_explanation_count": explanation_count,
            "source_page_dimensions_preserved": bool(
                (detected_layout or {}).get("source_dimensions_preserved")
            ),
            "source_column_layout_detected": any(
                doc.detected_column_count in {1, 2} for doc in documents
            ),
            "center_rule_supported": True,
            "personal_fonts_embedded": False,
            "source_fidelity_hwpx": True,
            "original_upload_bytes_preserved": True,
            "source_question_visuals_embedded": bool(
                (reconstruction_quality or {}).get("embedded_visual_question_count")
            ),
            "reconstruction_gate": (
                (reconstruction_quality or {}).get("gate") or "source_review_required"
            ),
        },
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2)


def _build_file_list_csv(
    input_files: list[dict[str, Any]],
    documents: list[TransferDocument],
    warnings: list[str],
    structure: TransferStructure,
) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["구분", "번호", "파일명", "원본", "유형", "크기/쪽수", "이미지", "본문글자", "OCR처리", "OCR대기", "상태", "경고"])
    for index, item in enumerate(input_files, start=1):
        writer.writerow(["원본", index, item["name"], "", item["kind"], item["sizeLabel"], "", "", "", "", "등록", ""])
    for index, doc in enumerate(documents, start=1):
        writer.writerow([
            "산출물",
            index,
            doc.filename,
            doc.source_name,
            doc.kind,
            doc.page_count,
            doc.image_count,
            doc.text_chars,
            doc.ocr_completed_units,
            doc.ocr_pending_units,
            _review_status(doc),
            doc.warning or "",
        ])
    for index, warning in enumerate(warnings, start=1):
        writer.writerow(["경고", index, "", "", "", "", "", "", "", "", "확인 필요", warning])
    writer.writerow([
        "검수본",
        1,
        "01_자체양식_문제검수본.doc",
        "",
        "자체양식",
        structure.structured_item_count,
        "",
        structure.text_chars,
        structure.ocr_completed_unit_count,
        structure.ocr_pending_unit_count,
        _quality_label(structure.quality_level),
        "",
    ])
    writer.writerow([
        "검수본",
        2,
        "03_자체양식_문제검수본.hwpx",
        "",
        "HWPX",
        structure.structured_item_count,
        "",
        structure.text_chars,
        structure.ocr_completed_unit_count,
        structure.ocr_pending_unit_count,
        _quality_label(structure.quality_level),
        "",
    ])
    writer.writerow([
        "검수본",
        3,
        "04_선생님문체_해설검수본.hwpx",
        "",
        "HWPX",
        structure.structured_problem_count,
        "",
        structure.text_chars,
        structure.ocr_completed_unit_count,
        structure.ocr_pending_unit_count,
        "정답·해설 검수 필요",
        "",
    ])
    writer.writerow([
        "검수본",
        4,
        "05_원본충실_레이아웃대조본.hwpx",
        "",
        "HWPX",
        sum(len(doc.source_pages) for doc in documents),
        "",
        "",
        "",
        "",
        "원본 시각 대조",
        "",
    ])
    return "\ufeff" + out.getvalue()


def _build_ocr_queue_csv(structure: TransferStructure) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["후보ID", "원본", "산출물", "유형", "쪽시작", "쪽끝", "남은단위", "우선순위", "사유", "권장처리"])
    if not structure.ocr_candidates:
        writer.writerow(["", "", "", "", "", "", "", "", "남은 OCR 후보 없음", ""])
    for item in structure.ocr_candidates:
        writer.writerow([
            item.get("candidate_id", ""),
            item.get("source_name", ""),
            item.get("filename", ""),
            item.get("kind", ""),
            item.get("page_start", ""),
            item.get("page_end", ""),
            item.get("estimated_units", ""),
            item.get("priority", ""),
            item.get("reason", ""),
            item.get("recommended_action", ""),
        ])
    return "\ufeff" + out.getvalue()


def _build_structured_workbook_html(
    meta: dict[str, str],
    structure: TransferStructure,
    document_style: dict[str, Any] | None = None,
) -> str:
    if structure.items:
        item_html = []
        for item in structure.items:
            choices = (
                f'<ol class="choices">{"".join(f"<li>{_rich_text_html(choice)}</li>" for choice in item.choices)}</ol>'
                if item.choices
                else '<p class="missing-field">보기 없음 · 원본 확인</p>'
            )
            flags = ", ".join(item.review_flags) if item.review_flags else "기본 검수"
            item_html.append(f"""
      <article class="problem-card">
        <div class="problem-head">
          <strong>{item.number}. {_escape("문제" if item.item_type == "problem" else "개념")}</strong>
          <span>{_escape(item.source_name)} · 신뢰도 {item.confidence:.2f} · {_escape(flags)}</span>
        </div>
        <div class="prompt">{_rich_text_html(item.prompt)}</div>
        {choices}
        <table class="answer-table">
          <tbody>
            <tr><th>정답</th><td>{_rich_text_html(item.answer or "검수 필요")}</td></tr>
            <tr><th>해설</th><td>{_rich_text_html(item.explanation or "검수 후 작성")}</td></tr>
          </tbody>
        </table>
      </article>
""")
        body_items = "\n".join(item_html)
    else:
        body_items = """
      <div class="warning">
        자동 분리할 텍스트가 없습니다. PDF/이미지 원본은 시각 이관 문서를 먼저 확인하고,
        자동 OCR 결과 또는 OCR 연결 후 문제 단위 편집본을 다시 생성하세요.
      </div>
"""
    ocr_rows = "\n".join(
        f"<tr><td>{_escape(item.get('candidate_id', index))}</td><td>{_escape(item['source_name'])}</td><td>{_escape(item['kind'])}</td><td>{item.get('page_start') or '-'}-{item.get('page_end') or '-'}</td><td>{_escape(item.get('priority', ''))}</td><td>{_escape(item['reason'])}</td></tr>"
        for index, item in enumerate(structure.ocr_candidates, start=1)
    ) or '<tr><td colspan="6">OCR 후보 없음</td></tr>'
    action_items = "".join(f"<li>{_escape(action)}</li>" for action in structure.review_actions)
    body = f"""
  <h1>{_escape(meta['title'])} 자체양식 문제검수본</h1>
  <div class="transfer-meta">
    <strong>반명</strong> {_escape(meta['class_name'] or "-")}<br />
    <strong>과목</strong> {_escape(meta['subject'] or "-")}<br />
    <strong>기준 양식</strong> {_escape(meta['template_name'])}<br />
    <strong>상태</strong> {_escape(_quality_label(structure.quality_level))}<br />
    <strong>자동 분리</strong> {structure.structured_item_count}개 · 문제 후보 {structure.structured_problem_count}개 · 개념 블록 {structure.concept_block_count}개<br />
    <strong>자동 OCR</strong> 처리 {structure.ocr_completed_unit_count}단위 · 남은 후보 {structure.ocr_candidate_count}개
  </div>
  <h2>검수 액션</h2>
  <ol>{action_items}</ol>
  <h2>문제/개념 단위 검수본</h2>
  {body_items}
  <h2>자동 OCR 및 연결 후보</h2>
  <table>
    <thead><tr><th>후보ID</th><th>원본</th><th>유형</th><th>쪽 범위</th><th>우선순위</th><th>사유</th></tr></thead>
    <tbody>{ocr_rows}</tbody>
  </table>
"""
    title_font = (
        document_style.get("title_font", {}).get("family_name", "함초롬돋움")
        if isinstance(document_style, dict)
        else "함초롬돋움"
    )
    body_font = (
        document_style.get("body_font", {}).get("family_name", "함초롬바탕")
        if isinstance(document_style, dict)
        else "함초롬바탕"
    )
    title_size = document_style.get("title_size_pt", 20) if isinstance(document_style, dict) else 20
    body_size = document_style.get("body_size_pt", 10.5) if isinstance(document_style, dict) else 10.5
    body_width_ratio = (
        document_style.get("body_width_ratio_percent", 100)
        if isinstance(document_style, dict)
        else 100
    )
    body_letter_spacing = (
        document_style.get("body_letter_spacing_percent", 0)
        if isinstance(document_style, dict)
        else 0
    )
    line_spacing = (
        document_style.get("line_spacing_percent", 155)
        if isinstance(document_style, dict)
        else 155
    )
    question_spacing = (
        document_style.get("question_spacing_pt", 10)
        if isinstance(document_style, dict)
        else 10
    )
    return _office_doc_shell(
        f"{meta['title']} 자체양식 문제검수본",
        body,
        extra_style=f"""
    body {{ font-family: {_css_string(body_font)}, "함초롬바탕", serif; font-size: {body_size}pt; font-stretch: {body_width_ratio}%; letter-spacing: {float(body_size) * float(body_letter_spacing) / 100:.2f}pt; line-height: {float(line_spacing) / 100:.2f}; }}
    h1 {{ font-family: {_css_string(title_font)}, "함초롬돋움", sans-serif; font-size: {title_size}pt; }}
    .problem-card {{ page-break-inside: avoid; border: 0.7pt solid #cbd5e1; padding: 4mm; margin: 0 0 {question_spacing}pt; }}
"""
        + """
    h2 { margin: 9mm 0 3mm; font-size: 13pt; }
    .problem-head { display: flex; justify-content: space-between; gap: 4mm; border-bottom: 0.5pt solid #e5e7eb; padding-bottom: 2mm; margin-bottom: 3mm; }
    .problem-head span { color: #64748b; font-size: 8.5pt; text-align: right; }
    .prompt { white-space: pre-wrap; margin: 0 0 3mm; }
    .choices { margin: 0 0 3mm 5mm; padding-left: 4mm; }
    .missing-field { margin: 0 0 3mm; color: #92400e; font-size: 9pt; }
    .answer-table, table { width: 100%; border-collapse: collapse; margin: 3mm 0 5mm; }
    .answer-table th, .answer-table td, table th, table td { border: 0.5pt solid #cbd5e1; padding: 2mm; vertical-align: top; font-size: 9pt; }
    .answer-table th, table th { width: 22mm; background: #f8fafc; }
""",
    )


def _structured_workbook_text_paragraphs(meta: dict[str, str], structure: TransferStructure) -> list[str]:
    paragraphs = [
        f"반명: {meta['class_name'] or '-'}",
        f"과목: {meta['subject'] or '-'}",
        f"기준 양식: {meta['template_name']}",
        f"상태: {_quality_label(structure.quality_level)}",
        f"자동 분리: {structure.structured_item_count}개 / 문제 후보 {structure.structured_problem_count}개 / 개념 블록 {structure.concept_block_count}개",
        f"자동 OCR: 처리 {structure.ocr_completed_unit_count}단위 / 남은 후보 {structure.ocr_candidate_count}개 / 남은 단위 {structure.ocr_pending_unit_count}",
        "",
        "[검수 액션]",
        *structure.review_actions,
        "",
        "[문제/개념 단위 검수본]",
    ]
    if structure.items:
        for item in structure.items:
            flags = ", ".join(item.review_flags) if item.review_flags else "기본 검수"
            choices = "\n".join(item.choices) if item.choices else "보기 없음 - 원본 확인"
            paragraphs.extend([
                "",
                f"{item.number}. {'문제' if item.item_type == 'problem' else '개념'} / {item.source_name} / 신뢰도 {item.confidence:.2f} / {flags}",
                item.prompt,
                choices,
                f"정답: {item.answer or '검수 필요'}",
                f"해설: {item.explanation or '검수 후 작성'}",
            ])
    else:
        paragraphs.append("자동 분리할 텍스트가 없습니다. PDF/이미지 원본은 시각 이관 문서를 먼저 확인하고 OCR 후보를 처리하세요.")

    paragraphs.extend(["", "[OCR 연결 후보]"])
    if structure.ocr_candidates:
        for item in structure.ocr_candidates:
            paragraphs.append(
                f"{item.get('candidate_id', '')} / {item.get('source_name', '')} / {item.get('kind', '')} "
                f"/ {item.get('page_start') or '-'}-{item.get('page_end') or '-'}쪽 "
                f"/ 남은 {item.get('estimated_units', '')}단위 / {item.get('reason', '')}"
            )
    else:
        paragraphs.append("남은 OCR 후보 없음")
    return paragraphs


def _build_structured_workbook_hwpx(
    meta: dict[str, str],
    structure: TransferStructure,
    documents: list[TransferDocument],
    document_style: dict[str, Any] | None = None,
) -> bytes:
    items = [
        {
            "number": item.number,
            "prompt": item.prompt,
            "choices": item.choices,
            "answer": item.answer,
            "explanation": item.explanation,
            "answer_check": item.answer_check,
        }
        for item in structure.items
        if item.item_type == "problem"
    ]
    meta_lines = [
        " · ".join(
            value
            for value in (
                meta["class_name"],
                meta["subject"],
                "원본 자동 재작성 · 배포 전 선생님 검수 필수",
            )
            if value
        )
    ]
    return build_hwpx_exam_document(
        title=meta["title"],
        meta_lines=meta_lines,
        items=items,
        document_style=document_style,
        solutions=False,
        question_visuals=_question_visual_map(documents),
    )


def _build_solution_workbook_hwpx(
    meta: dict[str, str],
    structure: TransferStructure,
    documents: list[TransferDocument],
    document_style: dict[str, Any] | None = None,
) -> bytes:
    items = [
        {
            "number": item.number,
            "prompt": item.prompt,
            "choices": item.choices,
            "answer": item.answer,
            "explanation": item.explanation,
            "answer_check": item.answer_check,
        }
        for item in structure.items
        if item.item_type == "problem"
    ]
    meta_lines = [
        " · ".join(
            value
            for value in (
                meta["class_name"],
                meta["subject"],
                "AI 작성 해설 · 배포 전 정답과 풀이 검수 필수",
            )
            if value
        )
    ]
    return build_hwpx_exam_document(
        title=f"{meta['title']} 해설",
        meta_lines=meta_lines,
        items=items,
        document_style=document_style,
        solutions=True,
        question_visuals=_question_visual_map(documents),
    )


def build_transfer_package(
    *,
    payload: dict[str, Any],
    source_files: Iterable[Any],
    ocr_context: TransferOcrContext | None = None,
    explanation_builder: Callable[[TransferStructure], list[dict[str, Any]]] | None = None,
) -> TransferPackage:
    meta = _payload_meta(payload)
    title = meta["title"]
    documents: list[TransferDocument] = []
    warnings: list[str] = []
    input_files: list[dict[str, Any]] = []
    original_files: list[tuple[str, bytes]] = []
    ocr_context = ocr_context or TransferOcrContext()

    for uploaded in source_files:
        name, data = _read_upload(uploaded)
        original_files.append((name, data))
        input_files.append({
            "name": name,
            "kind": _source_kind(name),
            "size": len(data),
            "sizeLabel": _source_size_label(len(data)),
        })
        if Path(name).suffix.lower() == ".zip":
            try:
                members, zip_warnings = _expand_zip(name, data)
                warnings.extend(zip_warnings)
            except Exception as exc:
                warning = f"{name}: ZIP 해제 중 오류가 발생했습니다. ({exc})"
                documents.append(_simple_text_transfer_doc(name, "", "ZIP 오류 리포트", warning))
                warnings.append(warning)
                continue
            for member_name, member_data in members:
                docs, doc_warnings = _docs_from_named_bytes(member_name, member_data, ocr_context=ocr_context)
                documents.extend(docs)
                warnings.extend(doc_warnings)
            continue

        docs, doc_warnings = _docs_from_named_bytes(name, data, ocr_context=ocr_context)
        documents.extend(docs)
        warnings.extend(doc_warnings)

    if not documents:
        warning = "이관할 원본 파일이 없습니다."
        documents.append(_simple_text_transfer_doc("empty.txt", "", "빈 요청", warning))
        warnings.append(warning)

    structure = analyze_transfer_documents(documents, warnings)
    source_tone_requested = payload.get("learn_source_explanation_style") is True
    source_tone_rights = payload.get("source_style_rights_confirmed") is True
    if source_tone_requested and not source_tone_rights:
        warnings.append("업로드 해설의 작성·사용 권한 확인이 없어 문체 샘플로 사용하지 않았습니다.")
    augmented_voice_profile = augment_voice_profile_with_source_items(
        payload.get("_resolved_voice_profile")
        if isinstance(payload.get("_resolved_voice_profile"), dict)
        else None,
        items=list(structure.items),
        enabled=source_tone_requested,
        rights_confirmed=source_tone_rights,
    )
    if augmented_voice_profile is not None:
        payload["_resolved_voice_profile"] = augmented_voice_profile
    if source_tone_requested and source_tone_rights and not (
        augmented_voice_profile
        and augmented_voice_profile.get("ephemeral_source_style_sample_count")
    ):
        warnings.append("업로드 자료에서 문체로 사용할 선생님 작성 해설을 찾지 못했습니다.")
    generated_explanations: list[dict[str, Any]] = []
    auto_explanations = payload.get("auto_explanations") is True
    if auto_explanations and explanation_builder and structure.structured_problem_count:
        try:
            generated_explanations = explanation_builder(structure)
        except Exception as exc:
            warnings.append(
                f"AI 해설 작성 중 오류가 발생해 전사 문제지만 생성했습니다. ({str(exc)[:180]})"
            )
    if auto_explanations and structure.structured_problem_count:
        generated_indexes = {
            int(item.get("index"))
            for item in generated_explanations
            if isinstance(item, dict) and str(item.get("index") or "").isdigit()
        }
        if len(generated_indexes) < structure.structured_problem_count:
            warnings.append(
                "일부 문항은 AI 해설을 만들지 못했습니다. 해설 검수본의 '검수 후 작성' 항목을 확인하세요."
            )
    structure = analyze_transfer_documents(documents, warnings)
    if generated_explanations:
        structure = apply_generated_explanations(structure, generated_explanations)
    explanation_count = sum(
        1 for item in structure.items if item.explanation_source == "teacher_voice_ai"
    )

    resolved_document_style = payload.get("_resolved_document_style")
    if not isinstance(resolved_document_style, dict):
        resolved_document_style = None
    document_style, detected_layout = _effective_document_style(
        resolved_document_style,
        documents,
    )
    reconstruction_quality = _reconstruction_quality(documents, structure)
    font_assets = custom_font_snapshots(resolved_document_style)
    now = datetime.now().strftime("%Y%m%d-%H%M%S")
    package_name = f"{_safe_filename(title, default='problem-studio')}_원본이관_{now}.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        used_names: set[str] = set()
        for index, doc in enumerate(documents, start=1):
            base_name = _safe_filename(doc.filename)
            zip_name = f"{index:02d}_{base_name}"
            while zip_name in used_names:
                zip_name = f"{index:02d}_{Path(base_name).stem}_{len(used_names) + 1}.doc"
            used_names.add(zip_name)
            zf.writestr(zip_name, "\ufeff" + doc.html)
        zf.writestr("00_먼저열기_검수체크리스트.doc", "\ufeff" + _build_review_checklist_html(meta, input_files, documents, warnings, structure))
        zf.writestr("00_변환리포트.html", _build_report_html(title, documents, warnings, structure))
        zf.writestr(
            "00_manifest.json",
            _build_manifest_json(
                meta,
                input_files,
                documents,
                warnings,
                structure,
                document_style=document_style,
                explanation_count=explanation_count,
                detected_layout=detected_layout,
                reconstruction_quality=reconstruction_quality,
            ),
        )
        zf.writestr("00_파일목록.csv", _build_file_list_csv(input_files, documents, warnings, structure))
        zf.writestr(
            "01_자체양식_문제검수본.doc",
            "\ufeff" + _build_structured_workbook_html(meta, structure, document_style),
        )
        zf.writestr("02_OCR_연결후보.csv", _build_ocr_queue_csv(structure))
        zf.writestr(
            "03_자체양식_문제검수본.hwpx",
            _build_structured_workbook_hwpx(
                meta,
                structure,
                documents,
                document_style,
            ),
        )
        zf.writestr(
            "04_선생님문체_해설검수본.hwpx",
            _build_solution_workbook_hwpx(
                meta,
                structure,
                documents,
                document_style,
            ),
        )
        zf.writestr(
            "05_원본충실_레이아웃대조본.hwpx",
            build_hwpx_source_fidelity_document(
                title=f"{meta['title']} 원본충실 레이아웃 대조본",
                source_pages=_source_page_payloads(documents),
            ),
        )
        original_names: set[str] = set()
        for index, (source_name, source_data) in enumerate(original_files, start=1):
            base_name = _safe_filename(source_name, default=f"원본_{index}")
            archive_name = f"06_업로드원본_그대로/{base_name}"
            if archive_name in original_names:
                archive_name = (
                    "06_업로드원본_그대로/"
                    f"{Path(base_name).stem}_{index}{Path(base_name).suffix}"
                )
            original_names.add(archive_name)
            zf.writestr(archive_name, source_data)

    return TransferPackage(
        filename=package_name,
        content_type="application/zip",
        data=buffer.getvalue(),
        documents=documents,
        warnings=warnings,
        review_file_count=10,
        structured_item_count=structure.structured_item_count,
        ocr_candidate_count=structure.ocr_candidate_count,
        quality_level=structure.quality_level,
        structure_limit_reached=structure.structure_limit_reached,
        font_assets=font_assets,
        explanation_count=explanation_count,
        detected_layout=detected_layout,
        reconstruction_quality=reconstruction_quality,
    )


def _build_report_html(
    title: str,
    documents: list[TransferDocument],
    warnings: list[str],
    structure: TransferStructure,
) -> str:
    rows = "\n".join(
        f"<tr>"
        f"<td>{index}</td>"
        f"<td>{_escape(doc.source_name)}</td>"
        f"<td>{_escape(doc.filename)}</td>"
        f"<td>{_escape(doc.kind)}</td>"
        f"<td>{doc.page_count}</td>"
        f"<td>{doc.image_count}</td>"
        f"<td>{doc.text_chars}</td>"
        f"<td>{doc.ocr_completed_units}/{doc.ocr_pending_units}</td>"
        f"<td>{_escape(doc.warning or '')}</td>"
        f"</tr>"
        for index, doc in enumerate(documents, start=1)
    )
    warning_items = "\n".join(f"<li>{_escape(w)}</li>" for w in warnings) or "<li>경고 없음</li>"
    body = f"""
  <h1>{_escape(title)} 변환 리포트</h1>
  <div class="transfer-meta">
    <strong>생성 시각</strong> {_escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}<br />
    <strong>문서 수</strong> {len(documents)}개<br />
    <strong>이미지/페이지 수</strong> {sum(doc.image_count for doc in documents)}개<br />
    <strong>자동 구조화</strong> {_escape(_quality_label(structure.quality_level))} · 항목 {structure.structured_item_count}개 · OCR 처리 {structure.ocr_completed_unit_count}단위 · 남은 OCR 후보 {structure.ocr_candidate_count}개
  </div>
  <h2>변환 결과</h2>
  <table>
    <thead>
      <tr><th>#</th><th>원본</th><th>산출물</th><th>유형</th><th>쪽수</th><th>이미지</th><th>본문 글자</th><th>OCR 처리/대기</th><th>경고</th></tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <h2>경고</h2>
  <ul>{warning_items}</ul>
  <h2>자동 검수 액션</h2>
  <ul>{"".join(f"<li>{_escape(action)}</li>" for action in structure.review_actions)}</ul>
"""
    return _office_doc_shell(
        f"{title} 변환 리포트",
        body,
        extra_style="""
    table { width: 100%; border-collapse: collapse; margin: 5mm 0; }
    th, td { border: 0.5pt solid #cbd5e1; padding: 2mm; vertical-align: top; font-size: 9pt; }
    th { background: #eef2ff; }
""",
    )


def package_to_response(package: TransferPackage) -> HttpResponse:
    response = HttpResponse(package.data, content_type=package.content_type)
    fallback = package.filename.encode("ascii", "ignore").decode("ascii") or "problem-studio-transfer.zip"
    response["Content-Disposition"] = f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(package.filename)}"
    response["X-Problem-Studio-Document-Count"] = str(len(package.documents))
    response["X-Problem-Studio-Warning-Count"] = str(len(package.warnings))
    response["X-Problem-Studio-Review-File-Count"] = str(package.review_file_count)
    response["X-Problem-Studio-Structured-Item-Count"] = str(package.structured_item_count)
    response["X-Problem-Studio-OCR-Candidate-Count"] = str(package.ocr_candidate_count)
    response["X-Problem-Studio-Quality-Level"] = package.quality_level
    response["X-Problem-Studio-Reconstruction-Gate"] = str(
        package.reconstruction_quality.get("gate") or "source_review_required"
    )
    return response
