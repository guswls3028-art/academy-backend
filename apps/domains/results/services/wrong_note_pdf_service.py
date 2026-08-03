from __future__ import annotations

import io
import logging
import os
import time
from collections import Counter
from typing import Any

from django.core.files.storage import default_storage
from django.utils import timezone

from apps.domains.results.services.wrong_note_service import (
    WrongNoteQuery,
    build_wrong_note_source_fingerprint,
    list_wrong_notes_for_enrollment,
)
from apps.infrastructure.storage.r2 import (
    delete_object_r2_storage,
    get_object_bytes_r2_storage,
    upload_fileobj_to_r2_storage,
)

logger = logging.getLogger(__name__)

_INK = "#172033"
_MUTED = "#667085"
_PAPER = "#F7F9FC"
_LINE = "#D8DEE9"
_ACCENT = "#4F46E5"
_WRONG = "#C24156"
_CORRECT = "#138A72"
MAX_WRONG_NOTE_PDF_ITEMS = 100
MAX_QUESTION_IMAGE_BYTES = 10 * 1024 * 1024
MAX_QUESTION_IMAGE_PIXELS = 20_000_000
PDF_GENERATION_DEADLINE_SECONDS = 90
PDF_OBJECT_CLEANUP_ATTEMPTS = 3


class WrongNotePDFEmptyError(ValueError):
    pass


class WrongNotePDFLimitError(ValueError):
    pass


class WrongNotePDFStaleError(ValueError):
    pass


class WrongNotePDFDeadlineError(TimeoutError):
    pass


def _ensure_korean_font() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular_name = "WrongNoteRegular"
    bold_name = "WrongNoteBold"
    try:
        pdfmetrics.getFont(regular_name)
        pdfmetrics.getFont(bold_name)
        return regular_name, bold_name
    except Exception:
        pass

    fonts_dir = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "assets",
            "omr",
            "renderer",
            "fonts",
        )
    )
    candidates = {
        regular_name: [
            os.path.join(fonts_dir, "NotoSansKR-Regular.ttf"),
            "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
        ],
        bold_name: [
            os.path.join(fonts_dir, "NotoSansKR-Bold.ttf"),
            "/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf",
        ],
    }
    registered: dict[str, bool] = {}
    for font_name, paths in candidates.items():
        registered[font_name] = False
        for path in paths:
            if not os.path.isfile(path):
                continue
            try:
                pdfmetrics.registerFont(TTFont(font_name, path))
                registered[font_name] = True
                break
            except Exception:
                continue

    if not registered[regular_name]:
        return "Helvetica", "Helvetica-Bold"
    if not registered[bold_name]:
        return regular_name, regular_name
    return regular_name, bold_name


def _remaining_seconds(deadline_monotonic: float | None) -> int | None:
    if deadline_monotonic is None:
        return None
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise WrongNotePDFDeadlineError(
            "PDF 생성 시간이 초과되었습니다. 시험 범위를 좁혀 다시 시도해 주세요."
        )
    return max(1, min(10, int(remaining)))


def _load_item_image(
    item: dict[str, Any],
    *,
    key_field: str,
    storage_name_field: str = "",
    deadline_monotonic: float | None = None,
    max_dimensions: tuple[int, int] = (1800, 1800),
):
    from PIL import Image, ImageOps

    data: bytes | None = None
    key = str(item.get(key_field) or "")
    storage_name = str(item.get(storage_name_field) or "") if storage_name_field else ""
    try:
        if key:
            data = get_object_bytes_r2_storage(
                key=key,
                max_bytes=MAX_QUESTION_IMAGE_BYTES,
                timeout_seconds=_remaining_seconds(deadline_monotonic),
            )
        elif storage_name:
            if default_storage.size(storage_name) > MAX_QUESTION_IMAGE_BYTES:
                raise ValueError("question image exceeds byte limit")
            with default_storage.open(storage_name, "rb") as image_file:
                data = image_file.read(MAX_QUESTION_IMAGE_BYTES + 1)
        if not data:
            return None
        if len(data) > MAX_QUESTION_IMAGE_BYTES:
            raise ValueError("question image exceeds byte limit")
        with Image.open(io.BytesIO(data)) as source:
            width, height = source.size
            if width < 1 or height < 1 or width * height > MAX_QUESTION_IMAGE_PIXELS:
                raise ValueError("question image exceeds pixel limit")
            image = ImageOps.exif_transpose(source).convert("RGB")
        _remaining_seconds(deadline_monotonic)
        if (
            image.width > max_dimensions[0]
            or image.height > max_dimensions[1]
        ):
            image.thumbnail(max_dimensions, Image.Resampling.LANCZOS)
        return image
    except WrongNotePDFDeadlineError:
        raise
    except Exception:
        logger.warning(
            "wrong-note question image load failed",
            extra={"question_id": item.get("question_id")},
            exc_info=True,
        )
        return None


def _load_question_image(item: dict[str, Any], *, deadline_monotonic=None):
    return _load_item_image(
        item,
        key_field="_question_image_key",
        storage_name_field="_question_image_name",
        deadline_monotonic=deadline_monotonic,
    )


def _load_explanation_image(item: dict[str, Any], *, deadline_monotonic=None):
    return _load_item_image(
        item,
        key_field="_explanation_image_key",
        deadline_monotonic=deadline_monotonic,
        max_dimensions=(2200, 8800),
    )


def _split_tall_image(image, *, max_aspect_ratio: float) -> list:
    """Split a tall source at low-ink rows so handwriting remains legible."""
    from PIL import Image, ImageChops, ImageOps

    def trim(source):
        rgb = source.convert("RGB")
        background = Image.new("RGB", rgb.size, "white")
        difference = ImageOps.grayscale(ImageChops.difference(rgb, background))
        mask = difference.point(lambda value: 255 if value > 10 else 0)
        bbox = mask.getbbox()
        background.close()
        difference.close()
        mask.close()
        if bbox is None:
            rgb.close()
            return None
        padding = max(8, round(min(rgb.size) * 0.015))
        left = max(0, bbox[0] - padding)
        top = max(0, bbox[1] - padding)
        right = min(rgb.width, bbox[2] + padding)
        bottom = min(rgb.height, bbox[3] + padding)
        cropped = rgb.crop((left, top, right, bottom))
        rgb.close()
        return cropped

    working = trim(image)
    if working is None:
        return []

    max_height = max(1, int(working.width * max_aspect_ratio))
    if working.height <= max_height:
        return [working]

    slices = []
    top = 0
    while top < working.height:
        target = min(top + max_height, working.height)
        if target >= working.height:
            bottom = working.height
        else:
            window_start = max(top + max_height // 2, target - max_height // 8)
            window_end = min(working.height - 1, target + max_height // 12)
            strip = working.crop((0, window_start, working.width, window_end + 1))
            scan_width = min(320, strip.width)
            scan_height = max(1, round(strip.height * scan_width / strip.width))
            scan = strip.resize((scan_width, scan_height), Image.Resampling.BILINEAR).convert("L")
            pixels = scan.load()
            row_ink = [
                sum(1 for x in range(scan.width) if pixels[x, y] < 242)
                for y in range(scan.height)
            ]
            if row_ink:
                target_row = round(
                    (target - window_start)
                    * scan.height
                    / max(strip.height, 1)
                )
                best_row = min(
                    range(len(row_ink)),
                    key=lambda row: (row_ink[row], abs(row - target_row)),
                )
                bottom = window_start + round(
                    best_row * strip.height / max(scan.height, 1)
                )
            else:
                bottom = target
            scan.close()
            strip.close()
            if bottom <= top + max_height // 3:
                bottom = target
        bottom = min(max(bottom, top + 1), working.height)
        part = working.crop((0, top, working.width, bottom))
        trimmed_part = trim(part)
        part.close()
        if trimmed_part is not None:
            slices.append(trimmed_part)
        top = bottom
    working.close()
    if not slices:
        return [image.copy()]

    merged = []
    gap = max(12, round(image.width * 0.025))
    for part in slices:
        if merged and merged[-1].height + gap + part.height <= round(max_height * 1.25):
            previous = merged.pop()
            width = max(previous.width, part.width)
            combined = Image.new(
                "RGB",
                (width, previous.height + gap + part.height),
                "white",
            )
            combined.paste(previous, ((width - previous.width) // 2, 0))
            combined.paste(
                part,
                ((width - part.width) // 2, previous.height + gap),
            )
            previous.close()
            part.close()
            merged.append(combined)
        else:
            merged.append(part)
    return merged


def _ellipsize(canvas, text: str, *, font_name: str, font_size: float, max_width: float) -> str:
    value = str(text or "")
    if canvas.stringWidth(value, font_name, font_size) <= max_width:
        return value
    suffix = "…"
    while value and canvas.stringWidth(value + suffix, font_name, font_size) > max_width:
        value = value[:-1]
    return value + suffix


def _session_label(item: dict[str, Any]) -> str:
    order = item.get("session_order")
    title = str(item.get("session_title") or "")
    if order is not None and title:
        if title.replace(" ", "") in {f"{order}주차", f"{order}회차"}:
            return title
        return f"{order}주차 · {title}"
    if order is not None:
        return f"{order}주차"
    return title or "주차 미지정"


def _sorted_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            int(item.get("session_order") or 10**9),
            int(item.get("exam_id") or 0),
            int(item.get("question_number") or 10**9),
        ),
    )


def build_wrong_note_pdf(
    *,
    enrollment: Any,
    tenant_name: str,
    items: list[dict[str, Any]],
    from_session_order: int,
    to_session_order: int | None = None,
    exam_id: int | None,
    deadline_monotonic: float | None = None,
) -> bytes:
    from PIL import Image
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    if not items:
        raise WrongNotePDFEmptyError("모을 오답이 없습니다.")

    rows = _sorted_items(items)
    regular_font, bold_font = _ensure_korean_font()
    generated_at = timezone.localtime()
    student_name = str(getattr(getattr(enrollment, "student", None), "name", "") or "학생")
    lecture_title = str(getattr(getattr(enrollment, "lecture", None), "title", "") or "")

    output = io.BytesIO()
    page_w, page_h = A4
    pdf = canvas.Canvas(output, pagesize=A4, pageCompression=1)
    pdf.setTitle(f"{student_name} 오답노트")
    pdf.setAuthor(tenant_name)

    # Cover
    pdf.setFillColor(HexColor(_INK))
    pdf.rect(0, page_h - 78 * mm, page_w, 78 * mm, fill=1, stroke=0)
    pdf.setFillColor(white)
    pdf.setFont(bold_font, 10)
    pdf.drawString(20 * mm, page_h - 22 * mm, tenant_name)
    pdf.setFont(bold_font, 30)
    pdf.drawString(20 * mm, page_h - 45 * mm, "오답노트")
    pdf.setFont(regular_font, 12)
    pdf.drawString(
        20 * mm,
        page_h - 58 * mm,
        "틀린 문항과 다시 볼 문항을 주차별로 묶었습니다.",
    )

    pdf.setFillColor(HexColor(_INK))
    pdf.setFont(bold_font, 19)
    pdf.drawString(20 * mm, page_h - 102 * mm, student_name)
    pdf.setFont(regular_font, 11)
    pdf.setFillColor(HexColor(_MUTED))
    pdf.drawString(20 * mm, page_h - 112 * mm, lecture_title)

    if exam_id is not None:
        scope_text = str(rows[0].get("exam_title") or "선택 시험")
    elif to_session_order is not None:
        scope_text = f"{from_session_order}~{to_session_order}회차"
    else:
        scope_text = f"{from_session_order}회차부터 누적"
    image_count = sum(1 for item in rows if item.get("has_question_image"))
    stats = [
        ("범위", scope_text),
        ("수록 문항", f"{len(rows)}문항"),
        ("문제 이미지", f"{image_count}/{len(rows)}문항"),
        ("생성일", generated_at.strftime("%Y.%m.%d")),
    ]
    card_y = page_h - 150 * mm
    card_w = (page_w - 46 * mm) / 2
    for idx, (label, value) in enumerate(stats):
        col = idx % 2
        row = idx // 2
        x = 20 * mm + col * (card_w + 6 * mm)
        y = card_y - row * 30 * mm
        pdf.setFillColor(HexColor(_PAPER))
        pdf.roundRect(x, y, card_w, 24 * mm, 3 * mm, fill=1, stroke=0)
        pdf.setFillColor(HexColor(_MUTED))
        pdf.setFont(regular_font, 9)
        pdf.drawString(x + 5 * mm, y + 15 * mm, label)
        pdf.setFillColor(HexColor(_INK))
        pdf.setFont(bold_font, 12)
        pdf.drawString(
            x + 5 * mm,
            y + 7 * mm,
            _ellipsize(
                pdf,
                value,
                font_name=bold_font,
                font_size=12,
                max_width=card_w - 10 * mm,
            ),
        )

    session_counts = Counter(_session_label(item) for item in rows)
    pdf.setFillColor(HexColor(_INK))
    pdf.setFont(bold_font, 11)
    pdf.drawString(20 * mm, 48 * mm, "수록 범위")
    pdf.setFont(regular_font, 9.5)
    pdf.setFillColor(HexColor(_MUTED))
    summary = "  ·  ".join(f"{label} {count}문항" for label, count in session_counts.items())
    pdf.drawString(
        20 * mm,
        39 * mm,
        _ellipsize(
            pdf,
            summary,
            font_name=regular_font,
            font_size=9.5,
            max_width=page_w - 40 * mm,
        ),
    )
    pdf.showPage()

    # One cropped question per page keeps tables and handwritten notation legible.
    for index, item in enumerate(rows, start=1):
        _remaining_seconds(deadline_monotonic)
        margin = 16 * mm
        content_w = page_w - 2 * margin
        pdf.setFillColor(HexColor(_INK))
        pdf.rect(0, page_h - 23 * mm, page_w, 23 * mm, fill=1, stroke=0)
        pdf.setFillColor(white)
        pdf.setFont(bold_font, 11)
        pdf.drawString(margin, page_h - 14 * mm, _session_label(item))
        pdf.setFont(regular_font, 9)
        exam_title = _ellipsize(
            pdf,
            str(item.get("exam_title") or "시험"),
            font_name=regular_font,
            font_size=9,
            max_width=90 * mm,
        )
        pdf.drawRightString(page_w - margin, page_h - 14 * mm, exam_title)

        q_number = item.get("question_number")
        question_label = f"{q_number}번" if q_number is not None else "문항 번호 미확인"
        pdf.setFillColor(HexColor(_INK))
        pdf.setFont(bold_font, 22)
        pdf.drawString(margin, page_h - 38 * mm, question_label)
        pdf.setFont(regular_font, 9)
        pdf.setFillColor(HexColor(_MUTED))
        pdf.drawRightString(page_w - margin, page_h - 36 * mm, f"{index} / {len(rows)}")

        image: Image.Image | None = _load_question_image(
            item,
            deadline_monotonic=deadline_monotonic,
        )
        image_top = page_h - 47 * mm
        image_x = margin
        max_draw_h = image_top - 76 * mm
        if image is None:
            image_h = 92 * mm
            image_y = image_top - image_h
            pdf.setStrokeColor(HexColor(_LINE))
            pdf.setLineWidth(0.8)
            pdf.roundRect(image_x, image_y, content_w, image_h, 3 * mm, fill=0, stroke=1)
            pdf.setDash(4, 3)
            pdf.setStrokeColor(HexColor(_LINE))
            pdf.roundRect(
                image_x + 9 * mm,
                image_y + 9 * mm,
                content_w - 18 * mm,
                image_h - 18 * mm,
                2 * mm,
                fill=0,
                stroke=1,
            )
            pdf.setDash()
            pdf.setFillColor(HexColor(_MUTED))
            pdf.setFont(bold_font, 12)
            pdf.drawCentredString(page_w / 2, image_y + image_h / 2 + 4 * mm, "문제 이미지 미등록")
            pdf.setFont(regular_font, 9)
            pdf.drawCentredString(
                page_w / 2,
                image_y + image_h / 2 - 4 * mm,
                "시험 설정의 이미지 등록에서 문항 사진을 추가할 수 있습니다.",
            )
        else:
            try:
                image_buffer = io.BytesIO()
                image.save(image_buffer, format="JPEG", quality=90, optimize=True)
                image_buffer.seek(0)
                image_w, raw_h = image.size
                available_w = content_w - 10 * mm
                scale = min(available_w / image_w, max_draw_h / raw_h)
                draw_w = image_w * scale
                draw_h = raw_h * scale
                image_h = draw_h + 10 * mm
                image_y = image_top - image_h
                pdf.setStrokeColor(HexColor(_LINE))
                pdf.setLineWidth(0.8)
                pdf.roundRect(
                    image_x,
                    image_y,
                    content_w,
                    image_h,
                    3 * mm,
                    fill=0,
                    stroke=1,
                )
                pdf.drawImage(
                    ImageReader(image_buffer),
                    image_x + (content_w - draw_w) / 2,
                    image_y + 5 * mm,
                    draw_w,
                    draw_h,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            finally:
                image.close()

        work_y = 29 * mm
        work_h = max(image_y - work_y - 8 * mm, 27 * mm)
        pdf.setFillColor(HexColor(_PAPER))
        pdf.roundRect(margin, work_y, content_w, work_h, 3 * mm, fill=1, stroke=0)
        pdf.setFillColor(HexColor(_MUTED))
        pdf.setFont(regular_font, 9)
        pdf.drawString(margin + 5 * mm, work_y + work_h - 8 * mm, "풀이 공간")
        pdf.setStrokeColor(HexColor(_LINE))
        pdf.setLineWidth(0.45)
        line_y = work_y + work_h - 18 * mm
        while line_y > work_y + 7 * mm:
            pdf.line(margin + 5 * mm, line_y, page_w - margin - 5 * mm, line_y)
            line_y -= 10 * mm

        pdf.setFillColor(HexColor(_MUTED))
        pdf.setFont(regular_font, 8)
        pdf.drawCentredString(page_w / 2, 12 * mm, f"{tenant_name} · {student_name} 오답노트")
        pdf.showPage()

    # Answers never leak into the worksheet section. Teacher-authored source
    # explanations are collected after a clear divider.
    pdf.setFillColor(HexColor(_INK))
    pdf.rect(0, 0, page_w, page_h, fill=1, stroke=0)
    pdf.setFillColor(white)
    pdf.setFont(bold_font, 28)
    pdf.drawString(20 * mm, page_h - 52 * mm, "정답 및 해설")
    pdf.setFont(regular_font, 12)
    pdf.drawString(20 * mm, page_h - 68 * mm, "선생님이 시험 원본에 작성한 해설을 그대로 수록했습니다.")
    pdf.showPage()

    for index, item in enumerate(rows, start=1):
        _remaining_seconds(deadline_monotonic)
        explanation_image: Image.Image | None = _load_explanation_image(
            item,
            deadline_monotonic=deadline_monotonic,
        )
        if explanation_image is None:
            explanation_parts = [None]
        else:
            explanation_parts = _split_tall_image(
                explanation_image,
                max_aspect_ratio=1.30,
            ) or [None]
            explanation_image.close()

        for part_index, explanation_part in enumerate(explanation_parts, start=1):
            _remaining_seconds(deadline_monotonic)
            margin = 16 * mm
            content_w = page_w - 2 * margin
            pdf.setFillColor(HexColor(_INK))
            pdf.rect(0, page_h - 23 * mm, page_w, 23 * mm, fill=1, stroke=0)
            pdf.setFillColor(white)
            pdf.setFont(bold_font, 11)
            section_label = "정답 및 해설"
            if len(explanation_parts) > 1:
                section_label += f" · {part_index}/{len(explanation_parts)}"
            pdf.drawString(margin, page_h - 14 * mm, section_label)
            pdf.setFont(regular_font, 9)
            pdf.drawRightString(page_w - margin, page_h - 14 * mm, f"{index} / {len(rows)}")

            q_number = item.get("question_number")
            pdf.setFillColor(HexColor(_INK))
            pdf.setFont(bold_font, 21)
            pdf.drawString(margin, page_h - 38 * mm, f"{q_number}번" if q_number else "문항")
            pdf.setFont(bold_font, 11)
            pdf.setFillColor(HexColor(_CORRECT))
            pdf.drawRightString(
                page_w - margin,
                page_h - 38 * mm,
                f"정답  {str(item.get('correct_answer') or '미등록')}",
            )

            image_x = margin
            image_y = 35 * mm
            image_h = page_h - 84 * mm
            pdf.setStrokeColor(HexColor(_LINE))
            pdf.roundRect(image_x, image_y, content_w, image_h, 3 * mm, fill=0, stroke=1)
            if explanation_part is not None:
                image_buffer = io.BytesIO()
                explanation_part.save(
                    image_buffer,
                    format="JPEG",
                    quality=92,
                    optimize=True,
                )
                image_buffer.seek(0)
                image_w, raw_h = explanation_part.size
                scale = min((content_w - 12 * mm) / image_w, (image_h - 12 * mm) / raw_h)
                draw_w = image_w * scale
                draw_h = raw_h * scale
                pdf.drawImage(
                    ImageReader(image_buffer),
                    image_x + (content_w - draw_w) / 2,
                    image_y + (image_h - draw_h) / 2,
                    draw_w,
                    draw_h,
                    preserveAspectRatio=True,
                    mask="auto",
                )
                explanation_part.close()
            else:
                explanation_text = str((item.get("extra") or {}).get("explanation_text") or "")
                pdf.setFillColor(HexColor(_MUTED))
                pdf.setFont(regular_font, 11)
                message = explanation_text or "등록된 선생님 해설이 없습니다."
                pdf.drawString(
                    image_x + 8 * mm,
                    image_y + image_h - 15 * mm,
                    _ellipsize(
                        pdf,
                        message,
                        font_name=regular_font,
                        font_size=11,
                        max_width=content_w - 16 * mm,
                    ),
                )

            pdf.setFillColor(HexColor(_MUTED))
            pdf.setFont(regular_font, 8)
            pdf.drawCentredString(page_w / 2, 12 * mm, f"{tenant_name} · {student_name} 오답노트")
            pdf.showPage()

    pdf.save()
    return output.getvalue()


def _hwpx_visual(source_image) -> dict[str, Any]:
    image = source_image.copy()
    try:
        if image.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
            converted = image.convert("RGB")
            image.close()
            image = converted
        output = io.BytesIO()
        image.save(output, format="PNG", compress_level=2)
        return {
            "data": output.getvalue(),
            "mime": "image/png",
            "width_px": image.width,
            "height_px": image.height,
        }
    finally:
        image.close()


def build_wrong_note_hwpx(
    *,
    enrollment: Any,
    tenant_name: str,
    items: list[dict[str, Any]],
    deadline_monotonic: float | None = None,
) -> bytes:
    from apps.domains.tools.problem_studio.hwpx_writer import (
        build_hwpx_editable_wrong_note_document,
    )

    if not items:
        raise WrongNotePDFEmptyError("모을 오답이 없습니다.")
    rows = _sorted_items(items)
    student_name = str(getattr(getattr(enrollment, "student", None), "name", "") or "학생")
    problem_pages: list[dict[str, Any]] = []
    for index, item in enumerate(rows, start=1):
        image = _load_question_image(item, deadline_monotonic=deadline_monotonic)
        try:
            problem_pages.append(
                {
                    "heading": f"{item.get('question_number') or index}번",
                    "subheading": (
                        f"{_session_label(item)} · "
                        f"{str(item.get('exam_title') or '시험')}"
                    ),
                    "visual": _hwpx_visual(image) if image is not None else None,
                }
            )
        finally:
            if image is not None:
                image.close()
    solution_pages: list[dict[str, Any]] = []
    for index, item in enumerate(rows, start=1):
        image = _load_explanation_image(item, deadline_monotonic=deadline_monotonic)
        if image is None:
            parts = [None]
        else:
            parts = _split_tall_image(image, max_aspect_ratio=1.30) or [None]
            image.close()
        for part_index, part in enumerate(parts, start=1):
            _remaining_seconds(deadline_monotonic)
            suffix = f" · {part_index}/{len(parts)}" if len(parts) > 1 else ""
            solution_pages.append(
                {
                    "heading": (
                        f"{item.get('question_number') or index}번 정답 및 해설"
                        f"{suffix}"
                    ),
                    "answer": str(item.get("correct_answer") or "미등록"),
                    "visual": _hwpx_visual(part) if part is not None else None,
                }
            )
            if part is not None:
                part.close()
    return build_hwpx_editable_wrong_note_document(
        title=f"{student_name} 오답노트",
        meta_lines=[
            f"{tenant_name} · 문제 {len(rows)}문항",
            "틀린 문제를 다시 풀고, 뒤쪽에서 선생님 해설을 확인하세요.",
        ],
        problem_pages=problem_pages,
        solution_pages=solution_pages,
    )


def wrong_note_pdf_storage_key(*, job: Any, tenant: Any) -> str:
    extension = str(getattr(job, "output_format", "") or "pdf")
    return f"tenants/{tenant.id}/results/wrong-notes/{job.id}.{extension}"


def generate_and_store_wrong_note_pdf(
    *,
    job: Any,
    enrollment: Any,
    tenant: Any,
) -> str:
    deadline_monotonic = time.monotonic() + PDF_GENERATION_DEADLINE_SECONDS
    total, items = list_wrong_notes_for_enrollment(
        enrollment_id=int(enrollment.id),
        q=WrongNoteQuery(
            exam_id=int(job.exam_id) if job.exam_id else None,
            lecture_id=int(job.lecture_id) if job.lecture_id else int(enrollment.lecture_id),
            from_session_order=int(job.from_session_order or 1),
            to_session_order=(
                int(job.to_session_order)
                if job.to_session_order is not None
                else None
            ),
            offset=0,
            limit=MAX_WRONG_NOTE_PDF_ITEMS,
        ),
    )
    if total > MAX_WRONG_NOTE_PDF_ITEMS:
        raise WrongNotePDFLimitError(
            f"오답이 {total}문항입니다. 시험 범위를 좁혀 "
            f"{MAX_WRONG_NOTE_PDF_ITEMS}문항 이하로 만들어 주세요."
        )
    expected_fingerprint = str(getattr(job, "source_fingerprint", "") or "")
    current_fingerprint = build_wrong_note_source_fingerprint(
        total=total,
        items=items,
    )
    if expected_fingerprint and current_fingerprint != expected_fingerprint:
        raise WrongNotePDFStaleError(
            "채점 또는 문항이 변경되었습니다. 최신 오답으로 다시 만들어 주세요."
        )

    if str(getattr(job, "output_format", "") or "pdf") == "hwpx":
        pdf_bytes = build_wrong_note_hwpx(
            enrollment=enrollment,
            tenant_name=str(getattr(tenant, "name", "") or "학원"),
            items=items,
            deadline_monotonic=deadline_monotonic,
        )
        content_type = "application/vnd.hancom.hwpx"
    else:
        pdf_bytes = build_wrong_note_pdf(
            enrollment=enrollment,
            tenant_name=str(getattr(tenant, "name", "") or "학원"),
            items=items,
            from_session_order=int(job.from_session_order or 1),
            to_session_order=(
                int(job.to_session_order)
                if job.to_session_order is not None
                else None
            ),
            exam_id=int(job.exam_id) if job.exam_id else None,
            deadline_monotonic=deadline_monotonic,
        )
        content_type = "application/pdf"
    upload_timeout = _remaining_seconds(deadline_monotonic)
    key = wrong_note_pdf_storage_key(job=job, tenant=tenant)
    upload_fileobj_to_r2_storage(
        fileobj=io.BytesIO(pdf_bytes),
        key=key,
        content_type=content_type,
        timeout_seconds=upload_timeout,
    )
    return key


def delete_wrong_note_pdf_object(key: str) -> bool:
    if not key:
        return True
    for attempt in range(1, PDF_OBJECT_CLEANUP_ATTEMPTS + 1):
        try:
            delete_object_r2_storage(key=key, timeout_seconds=3)
            return True
        except Exception:
            if attempt == PDF_OBJECT_CLEANUP_ATTEMPTS:
                logger.exception(
                    "wrong-note PDF object cleanup failed after retries",
                    extra={"key": key, "attempts": attempt},
                )
                return False
            logger.warning(
                "wrong-note PDF object cleanup retry scheduled",
                extra={"key": key, "attempt": attempt},
                exc_info=True,
            )
            time.sleep(0.25 * attempt)
    return False
