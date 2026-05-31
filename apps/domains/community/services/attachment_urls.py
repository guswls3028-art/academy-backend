from __future__ import annotations

from apps.infrastructure.storage import r2


def build_attachment_download_url(
    attachment,
    *,
    expires_in: int = 3600,
    force_download: bool = True,
) -> str | None:
    """Build the canonical presigned URL for a community attachment."""
    r2_key = getattr(attachment, "r2_key", "")
    if not r2_key:
        return None

    kwargs = {
        "key": r2_key,
        "expires_in": expires_in,
        "content_type": getattr(attachment, "content_type", None) or None,
    }
    if force_download:
        kwargs["filename"] = getattr(attachment, "original_name", "") or None
    return r2.generate_presigned_get_url_storage(**kwargs)
