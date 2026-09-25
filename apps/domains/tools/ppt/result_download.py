"""Issue a fresh PPT download URL from the completed job's private R2 key."""

from __future__ import annotations

import re


def refreshed_ppt_result(job, stored_result: dict, public_result: dict) -> dict:
    key = stored_result.get("r2_key")
    if key is None:
        # Results produced before this contract have only their original signed URL.
        return public_result
    filename = stored_result.get("filename")
    match = re.fullmatch(r"presentation_([0-9a-f]{12})\.pptx", filename or "") if isinstance(filename, str) else None
    if not (
        isinstance(key, str)
        and match
        and key == f"tenants/{job.tenant_id}/tools/ppt/{match.group(1)}.pptx"
    ):
        return {field: value for field, value in public_result.items() if field != "download_url"}

    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

    return {
        **public_result,
        "download_url": generate_presigned_get_url_storage(
            key=key,
            expires_in=3600,
            filename=filename,
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    }
