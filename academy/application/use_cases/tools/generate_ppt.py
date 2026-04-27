# PATH: academy/application/use_cases/tools/generate_ppt.py
# Use case orchestrators for PPT generation.
#
# - GeneratePptUseCase: from pre-processed images (existing flow)
# - GeneratePptFromPdfUseCase: from PDF with question splitting

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from PIL import Image as PILImage

logger = logging.getLogger(__name__)


@dataclass
class PptResult:
    """Result of PPT generation."""
    pptx_bytes: bytes
    slide_count: int


class GeneratePptUseCase:
    """Generate PPT from a list of image byte arrays.

    This is the existing flow: images are already preprocessed on the API side.
    The worker just assembles them into a PPT.
    """

    def execute(
        self,
        image_bytes_list: List[bytes],
        config: Optional[dict] = None,
        on_progress: Optional[Callable[[int, str], None]] = None,
    ) -> PptResult:
        """Execute PPT generation from images.

        Args:
            image_bytes_list: List of image bytes (JPEG/PNG).
            config: PPT config dict with keys: aspect_ratio, background, fit_mode.
            on_progress: Optional callback (percent, step_name).

        Returns:
            PptResult with PPTX bytes and slide count.
        """
        from academy.domain.tools.ppt_composer import PptComposer, PptConfig

        cfg = config or {}
        ppt_config = PptConfig(
            aspect_ratio=cfg.get("aspect_ratio", "16:9"),
            background=cfg.get("background", "black"),
            fit_mode=cfg.get("fit_mode", "contain"),
        )

        composer = PptComposer(ppt_config)
        total = len(image_bytes_list)

        for idx, img_bytes in enumerate(image_bytes_list):
            composer.add_slide(img_bytes)
            if on_progress:
                pct = int((idx + 1) / total * 100)
                on_progress(pct, f"슬라이드 {idx + 1}/{total}")

        pptx_bytes = composer.finalize()
        return PptResult(pptx_bytes=pptx_bytes, slide_count=composer.slide_count)


class GeneratePptFromPdfUseCase:
    """Generate PPT from a PDF by splitting questions and composing slides.

    Page-by-page streaming: renders one page, extracts questions, crops,
    preprocesses, and adds to PPT. Then releases the page image from memory.
    """

    def execute(
        self,
        pdf_path: str,
        config: Optional[dict] = None,
        on_progress: Optional[Callable[[int, str], None]] = None,
        image_settings: Optional[dict] = None,
    ) -> PptResult:
        """Execute PPT generation from PDF.

        Args:
            pdf_path: Path to the PDF file.
            config: PPT config dict with keys: aspect_ratio, background, fit_mode.
            on_progress: Optional callback (percent, step_name).

        Returns:
            PptResult with PPTX bytes and slide count.
        """
        from academy.adapters.tools.pymupdf_renderer import PdfDocument
        from academy.domain.tools.question_splitter import (
            split_questions,
            TextBlock as SplitterTextBlock,
        )
        from academy.domain.tools.image_preprocessor import preprocess_for_export, trim_bottom_whitespace
        from academy.domain.tools.ppt_composer import PptComposer, PptConfig

        # 빔프로젝터 1080p 충분 + Pillow MAX_IMAGE_PIXELS(50M) 안전. 고해상도 스캔본 대응.
        WHOLE_PAGE_MAX_LONG_EDGE = 2400

        cfg = config or {}
        ppt_config = PptConfig(
            aspect_ratio=cfg.get("aspect_ratio", "16:9"),
            background=cfg.get("background", "black"),
            fit_mode=cfg.get("fit_mode", "contain"),
        )

        composer = PptComposer(ppt_config)

        def _apply_user_settings(img_bytes: bytes) -> bytes:
            if not image_settings:
                return img_bytes
            if not any([
                image_settings.get("invert"),
                image_settings.get("grayscale"),
                image_settings.get("auto_enhance"),
                float(image_settings.get("brightness", 1.0)) != 1.0,
                float(image_settings.get("contrast", 1.0)) != 1.0,
            ]):
                return img_bytes
            from apps.domains.tools.ppt.services import _process_image
            return _process_image(
                img_bytes,
                invert=bool(image_settings.get("invert", False)),
                grayscale=bool(image_settings.get("grayscale", False)),
                auto_enhance=bool(image_settings.get("auto_enhance", False)),
                brightness=float(image_settings.get("brightness", 1.0)),
                contrast=float(image_settings.get("contrast", 1.0)),
            )

        with PdfDocument(pdf_path) as doc:
            page_count = doc.page_count()

            # Pre-pass: detect total text content to decide question-splitting vs whole-page mode.
            # 스캔/사진 PDF (text 레이어 없음)는 split_questions가 항상 0 regions 반환 → 전 페이지 skip → 0 슬라이드.
            # 이를 미리 감지해 페이지 단위 fallback로 전환.
            total_text_chars = 0
            for i in range(page_count):
                blocks = doc.extract_text_blocks(i)
                for b in blocks:
                    total_text_chars += len(b.text or "")
                    if total_text_chars >= 200:
                        break
                if total_text_chars >= 200:
                    break

            use_whole_page = total_text_chars < 200

            for page_idx in range(page_count):
                if on_progress:
                    pct = int(page_idx / max(page_count, 1) * 100)
                    on_progress(pct, f"페이지 {page_idx + 1}/{page_count}")

                if use_whole_page:
                    # 스캔/이미지 PDF: 페이지 = 슬라이드. 문항 인식 불가.
                    page_img = doc.render_page(page_idx, dpi=200)
                    if max(page_img.size) > WHOLE_PAGE_MAX_LONG_EDGE:
                        page_img.thumbnail(
                            (WHOLE_PAGE_MAX_LONG_EDGE, WHOLE_PAGE_MAX_LONG_EDGE),
                            resample=PILImage.LANCZOS,
                        )
                    export_img = preprocess_for_export(page_img)
                    img_bytes = _image_to_bytes(export_img)
                    img_bytes = _apply_user_settings(img_bytes)
                    composer.add_slide(img_bytes)
                    del page_img, export_img
                    continue

                # 텍스트 PDF: 문항 단위 분할
                page_w, page_h = doc.page_dimensions(page_idx)
                raw_blocks = doc.extract_text_blocks(page_idx)
                splitter_blocks = [
                    SplitterTextBlock(text=b.text, x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
                    for b in raw_blocks
                ]
                regions = split_questions(splitter_blocks, page_w, page_h, page_idx)
                if not regions:
                    continue

                page_img = doc.render_page(page_idx, dpi=200)
                img_w, img_h = page_img.size
                scale_x = img_w / page_w if page_w > 0 else 1.0
                scale_y = img_h / page_h if page_h > 0 else 1.0

                for region in regions:
                    rx0, ry0, rx1, ry1 = region.bbox
                    px0 = max(0, int(rx0 * scale_x))
                    py0 = max(0, int(ry0 * scale_y))
                    px1 = min(img_w, int(rx1 * scale_x))
                    py1 = min(img_h, int(ry1 * scale_y))
                    if px1 - px0 < 10 or py1 - py0 < 10:
                        continue
                    crop = page_img.crop((px0, py0, px1, py1))
                    crop = trim_bottom_whitespace(crop, padding_px=12)
                    export_img = preprocess_for_export(crop)
                    img_bytes = _image_to_bytes(export_img)
                    img_bytes = _apply_user_settings(img_bytes)
                    composer.add_slide(img_bytes)
                    del crop, export_img
                del page_img

            # 텍스트 모드로 갔지만 0 슬라이드인 경우 (예: 모든 페이지가 표지/목차로 분류된 short PDF):
            # 페이지 단위 fallback로 한 번 더 시도해야 사용자가 빈 결과를 보지 않음.
            if composer.slide_count == 0 and not use_whole_page:
                logger.info("PDF question-splitting yielded 0 slides - falling back to whole-page mode")
                for page_idx in range(page_count):
                    if on_progress:
                        on_progress(50 + int(page_idx / max(page_count, 1) * 50), f"페이지 단위 변환 {page_idx + 1}/{page_count}")
                    page_img = doc.render_page(page_idx, dpi=200)
                    if max(page_img.size) > WHOLE_PAGE_MAX_LONG_EDGE:
                        page_img.thumbnail(
                            (WHOLE_PAGE_MAX_LONG_EDGE, WHOLE_PAGE_MAX_LONG_EDGE),
                            resample=PILImage.LANCZOS,
                        )
                    export_img = preprocess_for_export(page_img)
                    img_bytes = _image_to_bytes(export_img)
                    img_bytes = _apply_user_settings(img_bytes)
                    composer.add_slide(img_bytes)
                    del page_img, export_img

        if composer.slide_count == 0:
            raise ValueError("No slides could be generated from the PDF")

        if on_progress:
            on_progress(100, "완료")

        pptx_bytes = composer.finalize()
        return PptResult(pptx_bytes=pptx_bytes, slide_count=composer.slide_count)


def _image_to_bytes(img, fmt: str = "PNG") -> bytes:
    """Convert PIL Image to bytes."""
    buf = io.BytesIO()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(buf, format=fmt, optimize=True)
    buf.seek(0)
    return buf.read()
