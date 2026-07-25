"""Static image previews for public matchup PDF reports.

The public pages only need one representative comparison page.  Rendering the
whole PDF in every visitor's browser is slow on mobile, so the backend renders
the first body page once and stores the JPEG beside the immutable/cached PDF.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable

from academy.adapters.tools.pymupdf_renderer import PdfBytesDocument

logger = logging.getLogger(__name__)

_PREVIEW_RENDER_VERSION = "v1"
_PREVIEW_ZOOM = 1.5
_PREVIEW_JPEG_QUALITY = 84


def preview_cache_key_for_pdf(pdf_key: str) -> str:
    """Return a deterministic derivative key without changing the source PDF."""
    return f"{pdf_key}.preview-{_PREVIEW_RENDER_VERSION}.jpg"


def render_matchup_pdf_preview(pdf_bytes: bytes) -> bytes:
    """Render the first comparison page, or page one for single-page uploads."""
    with PdfBytesDocument(pdf_bytes) as document:
        if document.page_count() < 1:
            raise ValueError("PDF has no pages")
        page_index = 1 if document.page_count() > 1 else 0
        content_type, image_bytes = document.render_page_bytes(
            page_index,
            zoom=_PREVIEW_ZOOM,
            jpg_quality=_PREVIEW_JPEG_QUALITY,
        )

    if content_type == "image/jpeg":
        return image_bytes

    # PyMuPDF versions without direct JPEG quality support return PNG.  Normalize
    # to JPEG so the public endpoint and cache key keep one stable media contract.
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as image:
        rgb = image.convert("RGB")
        output = io.BytesIO()
        rgb.save(output, format="JPEG", quality=_PREVIEW_JPEG_QUALITY, optimize=True)
        return output.getvalue()


def get_or_create_matchup_preview(
    *,
    pdf_key: str,
    load_pdf_bytes: Callable[[], bytes],
) -> tuple[bytes, str]:
    """Return cached JPEG bytes and cache state without mutating report rows."""
    from apps.infrastructure.storage.r2 import (
        get_object_bytes_r2_storage,
        upload_fileobj_to_r2_storage,
    )

    preview_key = preview_cache_key_for_pdf(pdf_key)
    try:
        cached = get_object_bytes_r2_storage(key=preview_key)
        if cached:
            return cached, "hit"
    except Exception:
        logger.warning(
            "MATCHUP_PREVIEW_CACHE_READ_FAIL | key=%s",
            preview_key,
            exc_info=True,
        )

    preview_bytes = render_matchup_pdf_preview(load_pdf_bytes())
    try:
        upload_fileobj_to_r2_storage(
            fileobj=io.BytesIO(preview_bytes),
            key=preview_key,
            content_type="image/jpeg",
        )
    except Exception:
        logger.warning(
            "MATCHUP_PREVIEW_CACHE_WRITE_FAIL | key=%s",
            preview_key,
            exc_info=True,
        )
        return preview_bytes, "bypass"
    return preview_bytes, "miss"
