"""Static image previews for public matchup PDF reports.

The public pages only need one representative comparison page.  Rendering the
whole PDF in every visitor's browser is slow on mobile, so the backend renders
the first body page once and stores the JPEG beside the immutable/cached PDF.
"""

from __future__ import annotations

import io
import hashlib
import logging
from collections.abc import Callable

from academy.adapters.tools.pymupdf_renderer import PdfBytesDocument

logger = logging.getLogger(__name__)

_PREVIEW_RENDER_VERSION = "v2"
_PREVIEW_ZOOM = 1.5
_PREVIEW_JPEG_QUALITY = 84
_PREVIEW_MAX_SIDE = 4096
_PREVIEW_MAX_PIXELS = 12_000_000
_PREVIEW_BOUND_MARGIN = 0.999


def preview_cache_key_for_pdf(pdf_key: str) -> str:
    """Return a deterministic derivative key without changing the source PDF."""
    return f"{pdf_key}.preview-{_PREVIEW_RENDER_VERSION}.jpg"


def preview_etag_for_pdf(pdf_key: str, *, namespace: str = "matchup-preview") -> str:
    """Return an ETag that changes with both the PDF key and render version."""
    preview_key = preview_cache_key_for_pdf(pdf_key)
    digest = hashlib.sha256(preview_key.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f'W/"{namespace}-{digest}"'


def _preview_log_id(pdf_key: str) -> str:
    return hashlib.sha256(pdf_key.encode("utf-8", errors="replace")).hexdigest()[:12]


def get_cached_matchup_preview(*, pdf_key: str) -> bytes | None:
    """Read a prepared JPEG without doing PDF work in a public request."""
    from apps.infrastructure.storage.r2 import get_object_bytes_r2_storage

    preview_key = preview_cache_key_for_pdf(pdf_key)
    try:
        return get_object_bytes_r2_storage(key=preview_key)
    except Exception:
        logger.warning(
            "MATCHUP_PREVIEW_CACHE_READ_FAIL | object=%s",
            _preview_log_id(preview_key),
            exc_info=True,
        )
        return None


def store_matchup_preview(*, pdf_key: str, preview_bytes: bytes) -> None:
    """Persist a validated JPEG derivative under the versioned cache key."""
    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

    upload_fileobj_to_r2_storage(
        fileobj=io.BytesIO(preview_bytes),
        key=preview_cache_key_for_pdf(pdf_key),
        content_type="image/jpeg",
    )


def delete_matchup_preview_assets(*, pdf_key: str, include_source: bool = True) -> None:
    """Best-effort cleanup for a snapshot that failed before DB publication."""
    from apps.infrastructure.storage.r2 import delete_object_r2_storage

    keys = [preview_cache_key_for_pdf(pdf_key)]
    if include_source:
        keys.append(pdf_key)
    for key in keys:
        try:
            delete_object_r2_storage(key=key)
        except Exception:
            logger.warning(
                "MATCHUP_PREVIEW_ORPHAN_CLEANUP_FAIL | object=%s",
                _preview_log_id(key),
                exc_info=True,
            )


def render_matchup_pdf_preview(
    pdf_bytes: bytes,
    *,
    first_body_page: bool = True,
) -> bytes:
    """Render one comparison page within a bounded output size."""
    with PdfBytesDocument(pdf_bytes) as document:
        if document.page_count() < 1:
            raise ValueError("PDF has no pages")
        page_index = 1 if first_body_page and document.page_count() > 1 else 0
        page_width, page_height = document.page_size(page_index)
        if page_width <= 0 or page_height <= 0:
            raise ValueError("PDF page has invalid dimensions")
        zoom = min(
            _PREVIEW_ZOOM,
            (_PREVIEW_MAX_SIDE / max(page_width, page_height)) * _PREVIEW_BOUND_MARGIN,
            ((_PREVIEW_MAX_PIXELS / (page_width * page_height)) ** 0.5)
            * _PREVIEW_BOUND_MARGIN,
        )
        content_type, image_bytes = document.render_page_bytes(
            page_index,
            zoom=zoom,
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
    first_body_page: bool = True,
    require_cache_write: bool = False,
) -> tuple[bytes, str]:
    """Return cached JPEG bytes and cache state without mutating report rows."""
    preview_key = preview_cache_key_for_pdf(pdf_key)
    cached = get_cached_matchup_preview(pdf_key=pdf_key)
    if cached:
        return cached, "hit"

    preview_bytes = render_matchup_pdf_preview(
        load_pdf_bytes(),
        first_body_page=first_body_page,
    )
    try:
        store_matchup_preview(pdf_key=pdf_key, preview_bytes=preview_bytes)
    except Exception:
        if require_cache_write:
            raise
        logger.warning(
            "MATCHUP_PREVIEW_CACHE_WRITE_FAIL | object=%s",
            _preview_log_id(preview_key),
            exc_info=True,
        )
        return preview_bytes, "bypass"
    return preview_bytes, "miss"
