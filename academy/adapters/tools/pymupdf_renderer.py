# PATH: academy/adapters/tools/pymupdf_renderer.py
# PDF rendering via PyMuPDF (fitz).
#
# PdfDocument context manager: opens once, exposes all operations.
# Standalone functions kept for backward compatibility.

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from PIL import Image


@dataclass
class TextBlock:
    """Text block from a PDF page with bounding box."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


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
