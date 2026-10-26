"""Bound raster allocation before MuPDF allocates a user-supplied PDF page."""
import math

import fitz
import pytest

from academy.adapters.tools.pymupdf_renderer import PdfDocument


@pytest.mark.parametrize("width,height", [(14400, 14400), (14400, 72), (72, 14400)])
def test_large_pages_are_bounded_before_native_allocation(tmp_path, monkeypatch, width, height):
    path = tmp_path / "large.pdf"
    with fitz.open() as source:
        page = source.new_page(width=width, height=height)
        page.draw_rect(page.rect, color=None, fill=(1, 0, 0))
        source.save(path)
    native_render = fitz.Page.get_pixmap

    def checked_render(page, *, matrix, **kwargs):
        pixels = math.ceil(page.rect.width * matrix.a) * math.ceil(page.rect.height * matrix.d)
        assert pixels <= 20_000_000
        assert max(page.rect.width * matrix.a, page.rect.height * matrix.d) <= 8192
        return native_render(page, matrix=matrix, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_pixmap", checked_render)
    with PdfDocument(str(path)) as document:
        image = document.render_page(0, dpi=300)
    assert image.mode == "RGB"
    assert image.width * image.height <= 20_000_000
    # The whole page survives; it is scaled rather than cropped.
    assert image.getpixel((image.width // 2, image.height // 2)) == (255, 0, 0)
    assert image.width / image.height == pytest.approx(width / height, rel=0.03)


def test_a4_omr_keeps_requested_300_dpi(tmp_path):
    path = tmp_path / "a4.pdf"
    with fitz.open() as source:
        source.new_page(width=842, height=595)
        source.save(path)
    with PdfDocument(str(path)) as document:
        image = document.render_page(0, dpi=300)
    assert image.size == (3509, 2480)


@pytest.mark.parametrize("dpi", [0, -1, float("nan"), float("inf")])
def test_invalid_resolution_fails_before_render(tmp_path, monkeypatch, dpi):
    path = tmp_path / "input.pdf"
    with fitz.open() as source:
        source.new_page()
        source.save(path)
    monkeypatch.setattr(fitz.Page, "get_pixmap", lambda *a, **k: pytest.fail("invalid native allocation"))
    with PdfDocument(str(path)) as document, pytest.raises(ValueError):
        document.render_page(0, dpi=dpi)
