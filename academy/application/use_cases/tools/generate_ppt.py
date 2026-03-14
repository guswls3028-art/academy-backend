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
    ) -> PptResult:
        """Execute PPT generation from PDF.

        Args:
            pdf_path: Path to the PDF file.
            config: PPT config dict with keys: aspect_ratio, background, fit_mode.
            on_progress: Optional callback (percent, step_name).

        Returns:
            PptResult with PPTX bytes and slide count.
        """
        from academy.adapters.tools.pymupdf_renderer import (
            render_page,
            extract_text_blocks,
            get_page_count,
            get_page_dimensions,
        )
        from academy.domain.tools.question_splitter import (
            split_questions,
            TextBlock as SplitterTextBlock,
        )
        from academy.domain.tools.image_preprocessor import preprocess_for_export
        from academy.domain.tools.ppt_composer import PptComposer, PptConfig

        cfg = config or {}
        ppt_config = PptConfig(
            aspect_ratio=cfg.get("aspect_ratio", "16:9"),
            background=cfg.get("background", "black"),
            fit_mode=cfg.get("fit_mode", "contain"),
        )

        composer = PptComposer(ppt_config)
        page_count = get_page_count(pdf_path)

        for page_idx in range(page_count):
            if on_progress:
                pct = int(page_idx / max(page_count, 1) * 100)
                on_progress(pct, f"페이지 {page_idx + 1}/{page_count}")

            # Get page dimensions (in PDF points)
            page_w, page_h = get_page_dimensions(pdf_path, page_idx)

            # Extract text blocks for question detection
            raw_blocks = extract_text_blocks(pdf_path, page_idx)
            splitter_blocks = [
                SplitterTextBlock(
                    text=b.text,
                    x0=b.x0,
                    y0=b.y0,
                    x1=b.x1,
                    y1=b.y1,
                )
                for b in raw_blocks
            ]

            # Split into question regions
            regions = split_questions(splitter_blocks, page_w, page_h, page_idx)

            if not regions:
                # No questions found — treat whole page as one slide
                page_img = render_page(pdf_path, page_idx, dpi=200)
                export_img = preprocess_for_export(page_img)
                img_bytes = _image_to_bytes(export_img)
                composer.add_slide(img_bytes)
                del page_img, export_img
                continue

            # Render the page image
            page_img = render_page(pdf_path, page_idx, dpi=200)
            img_w, img_h = page_img.size

            # Scale factor: PDF points -> rendered pixels
            scale_x = img_w / page_w if page_w > 0 else 1.0
            scale_y = img_h / page_h if page_h > 0 else 1.0

            for region in regions:
                # Convert bbox from PDF points to pixel coordinates
                rx0, ry0, rx1, ry1 = region.bbox
                px0 = max(0, int(rx0 * scale_x))
                py0 = max(0, int(ry0 * scale_y))
                px1 = min(img_w, int(rx1 * scale_x))
                py1 = min(img_h, int(ry1 * scale_y))

                # Skip degenerate regions
                if px1 - px0 < 10 or py1 - py0 < 10:
                    continue

                crop = page_img.crop((px0, py0, px1, py1))
                export_img = preprocess_for_export(crop)
                img_bytes = _image_to_bytes(export_img)
                composer.add_slide(img_bytes)
                del crop, export_img

            # Release page image memory
            del page_img

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
