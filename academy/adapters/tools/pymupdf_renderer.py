# PATH: academy/adapters/tools/pymupdf_renderer.py
# PDF rendering via PyMuPDF (fitz).
#
# PdfDocument context manager: opens once, exposes all operations.
# Standalone functions kept for backward compatibility.

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from collections.abc import Sequence
from typing import List, Tuple

from PIL import Image

PdfTextLine = tuple[float, float, str, float]


@dataclass
class TextBlock:
    """Text block from a PDF page with bounding box."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class VisualBlock:
    """Non-text page block with a PDF-space bounding box."""

    x0: float
    y0: float
    x1: float
    y1: float


def has_vertical_center_rule(image_data: bytes) -> bool:
    """Return whether a raster page contains a long rule near its center."""

    try:
        import cv2
        import numpy as np

        image_bgr = cv2.imdecode(
            np.frombuffer(image_data, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if image_bgr is None:
            return False
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape[:2]
        edges = cv2.Canny(gray, 60, 160)
        lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 360,
            threshold=max(40, height // 20),
            minLineLength=max(120, int(height * 0.24)),
            maxLineGap=max(20, int(height * 0.10)),
        )
        if lines is None:
            return False
        for raw_line in lines.reshape(-1, 4):
            x1, y1, x2, y2 = (int(value) for value in raw_line)
            midpoint_x = (x1 + x2) / 2
            if (
                width * 0.40 <= midpoint_x <= width * 0.60
                and abs(x2 - x1) <= max(width * 0.12, abs(y2 - y1) * 0.12)
                and abs(y2 - y1) >= height * 0.24
            ):
                return True
    except Exception:
        return False
    return False


class PdfDocument:
    """Context manager that opens a PDF once and exposes all page operations.

    Usage:
        with PdfDocument(pdf_path) as doc:
            for i in range(doc.page_count()):
                w, h = doc.page_dimensions(i)
                blocks = doc.extract_text_blocks(i)
                img = doc.render_page(i, dpi=200)
    """

    def __init__(self, pdf_path: str):
        import fitz  # PyMuPDF
        self._doc = fitz.open(pdf_path)

    def __enter__(self) -> PdfDocument:
        return self

    def __exit__(self, *_) -> None:
        self._doc.close()

    def page_count(self) -> int:
        """Get total number of pages."""
        return len(self._doc)

    def page_dimensions(self, page_index: int) -> Tuple[float, float]:
        """Get page dimensions (width, height) in PDF points."""
        if page_index < 0 or page_index >= len(self._doc):
            raise IndexError(
                f"Page index {page_index} out of range (0-{len(self._doc) - 1})"
            )
        page = self._doc[page_index]
        rect = page.rect
        return rect.width, rect.height

    def extract_text_blocks(self, page_index: int) -> List[TextBlock]:
        """Extract text blocks with positions from a single PDF page."""
        if page_index < 0 or page_index >= len(self._doc):
            raise IndexError(
                f"Page index {page_index} out of range (0-{len(self._doc) - 1})"
            )
        page = self._doc[page_index]
        raw_blocks = page.get_text("blocks")

        result: List[TextBlock] = []
        for block in raw_blocks:
            if block[6] == 0:  # text block (0=text, 1=image)
                text = block[4].strip()
                if text:
                    result.append(TextBlock(
                        text=text,
                        x0=block[0],
                        y0=block[1],
                        x1=block[2],
                        y1=block[3],
                    ))
        return result

    def extract_text_words(self, page_index: int) -> List[TextBlock]:
        """Extract individual text words with positions from a single PDF page."""
        if page_index < 0 or page_index >= len(self._doc):
            raise IndexError(
                f"Page index {page_index} out of range (0-{len(self._doc) - 1})"
            )
        page = self._doc[page_index]
        raw_words = page.get_text("words")

        result: List[TextBlock] = []
        for word in raw_words:
            text = str(word[4]).strip()
            if text:
                result.append(TextBlock(
                    text=text,
                    x0=word[0],
                    y0=word[1],
                    x1=word[2],
                    y1=word[3],
                ))
        return result

    def render_page(self, page_index: int, dpi: int = 200) -> Image.Image:
        """Render a single PDF page as a PIL Image (RGB)."""
        import fitz  # PyMuPDF

        if page_index < 0 or page_index >= len(self._doc):
            raise IndexError(
                f"Page index {page_index} out of range (0-{len(self._doc) - 1})"
            )
        page = self._doc[page_index]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return img


class PdfBytesDocument:
    """Context manager for PDF bytes used by HTTP upload pipelines."""

    def __init__(self, data: bytes):
        import fitz  # PyMuPDF

        self._fitz = fitz
        self._doc = fitz.open(stream=data, filetype="pdf")

    def __enter__(self) -> PdfBytesDocument:
        return self

    def __exit__(self, *_) -> None:
        self._doc.close()

    def page_count(self) -> int:
        return int(self._doc.page_count)

    def extract_text(self) -> str:
        return "\n\n".join(page.get_text("text") or "" for page in self._doc)

    def extract_page_text(self, page_index: int) -> str:
        if page_index < 0 or page_index >= self.page_count():
            raise IndexError(f"Page index {page_index} out of range (0-{self.page_count() - 1})")
        return self._doc[page_index].get_text("text") or ""

    def extract_page_text_blocks(self, page_index: int) -> List[TextBlock]:
        if page_index < 0 or page_index >= self.page_count():
            raise IndexError(f"Page index {page_index} out of range (0-{self.page_count() - 1})")
        blocks: List[TextBlock] = []
        for block in self._doc[page_index].get_text("blocks"):
            if len(block) < 7 or block[6] != 0:
                continue
            text = str(block[4] or "").strip()
            if text:
                blocks.append(TextBlock(
                    text=text,
                    x0=float(block[0]),
                    y0=float(block[1]),
                    x1=float(block[2]),
                    y1=float(block[3]),
                ))
        return blocks

    def extract_page_visual_blocks(self, page_index: int) -> List[VisualBlock]:
        if page_index < 0 or page_index >= self.page_count():
            raise IndexError(f"Page index {page_index} out of range (0-{self.page_count() - 1})")
        blocks: List[VisualBlock] = []
        for block in self._doc[page_index].get_text("dict").get("blocks", []):
            if int(block.get("type", 0)) != 1:
                continue
            bbox = block.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            blocks.append(VisualBlock(
                x0=float(bbox[0]),
                y0=float(bbox[1]),
                x1=float(bbox[2]),
                y1=float(bbox[3]),
            ))
        return blocks

    def page_size(self, page_index: int) -> tuple[float, float]:
        if page_index < 0 or page_index >= self.page_count():
            raise IndexError(f"Page index {page_index} out of range (0-{self.page_count() - 1})")
        rect = self._doc[page_index].rect
        return float(rect.width), float(rect.height)

    def render_page_bytes(self, page_index: int, *, zoom: float, jpg_quality: int = 82) -> tuple[str, bytes]:
        if page_index < 0 or page_index >= self.page_count():
            raise IndexError(f"Page index {page_index} out of range (0-{self.page_count() - 1})")
        page = self._doc[page_index]
        matrix = self._fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        try:
            return "image/jpeg", pix.tobytes("jpeg", jpg_quality=jpg_quality)
        except TypeError:
            return "image/png", pix.tobytes("png")


def extract_pdf_text_from_bytes(data: bytes) -> str:
    with PdfBytesDocument(data) as doc:
        return doc.extract_text()


def get_page_count_from_bytes(data: bytes) -> int:
    with PdfBytesDocument(data) as doc:
        return doc.page_count()


def create_pdf_file(
    *,
    pages: Sequence[Sequence[PdfTextLine]] | None = None,
    suffix: str = ".pdf",
    width: float = 595,
    height: float = 842,
) -> str:
    import fitz  # PyMuPDF

    doc = fitz.open()
    try:
        for lines in pages or [[]]:
            page = doc.new_page(width=width, height=height)
            for x, y, text, font_size in lines:
                page.insert_text((x, y), text, fontsize=font_size)
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.close()
        doc.save(tmp.name)
        return tmp.name
    finally:
        doc.close()


def create_text_pdf_file(
    text_lines: Sequence[str],
    *,
    suffix: str = ".pdf",
    x: float = 50,
    y_start: float = 100,
    y_step: float = 60,
    font_size: float = 10,
    width: float = 595,
    height: float = 842,
) -> str:
    lines = [
        (x, y_start + i * y_step, text, font_size)
        for i, text in enumerate(text_lines)
    ]
    return create_pdf_file(pages=[lines], suffix=suffix, width=width, height=height)


def create_blank_pdf_file(*, page_count: int = 1, suffix: str = ".pdf", width: float = 595, height: float = 842) -> str:
    return create_pdf_file(pages=[[] for _ in range(page_count)], suffix=suffix, width=width, height=height)


def create_blank_pdf_bytes(*, page_count: int = 1, width: float = 595, height: float = 842) -> bytes:
    import fitz  # PyMuPDF

    doc = fitz.open()
    try:
        for _ in range(page_count):
            doc.new_page(width=width, height=height)
        return doc.tobytes()
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Standalone functions (backward compatibility)
# ---------------------------------------------------------------------------

def render_page(pdf_path: str, page_index: int, dpi: int = 200) -> Image.Image:
    """Render a single PDF page as a PIL Image.

    Opens the PDF, renders only the requested page, then closes.

    Args:
        pdf_path: Path to the PDF file.
        page_index: 0-based page index.
        dpi: Rendering resolution. Default 200.

    Returns:
        PIL Image (RGB).
    """
    with PdfDocument(pdf_path) as doc:
        return doc.render_page(page_index, dpi)


def extract_text_blocks(pdf_path: str, page_index: int) -> List[TextBlock]:
    """Extract text blocks with positions from a single PDF page.

    Args:
        pdf_path: Path to the PDF file.
        page_index: 0-based page index.

    Returns:
        List of TextBlock with text content and bounding coordinates.
    """
    with PdfDocument(pdf_path) as doc:
        return doc.extract_text_blocks(page_index)


def get_page_count(pdf_path: str) -> int:
    """Get total page count of a PDF.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Number of pages.
    """
    with PdfDocument(pdf_path) as doc:
        return doc.page_count()


def get_page_dimensions(pdf_path: str, page_index: int) -> Tuple[float, float]:
    """Get page dimensions (width, height) in PDF points.

    Args:
        pdf_path: Path to the PDF file.
        page_index: 0-based page index.

    Returns:
        (width, height) in points.
    """
    with PdfDocument(pdf_path) as doc:
        return doc.page_dimensions(page_index)
