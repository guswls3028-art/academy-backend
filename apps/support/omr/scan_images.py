from __future__ import annotations

from typing import Any, Dict


def select_omr_scan_image(
    *,
    submission_meta: Dict[str, Any] | None,
    original_file_key: str | None,
    tenant_id: int | None = None,
) -> Dict[str, Any]:
    """
    Pick the scan image key the UI should display.

    AI keeps the immutable original upload, then stores an aligned JPEG preview
    when grading succeeds. Prefer that aligned page because review BBox
    coordinates are computed on the aligned image.
    """
    meta = submission_meta if isinstance(submission_meta, dict) else {}
    ai_result = meta.get("ai_result") if isinstance(meta, dict) else None
    result = ai_result.get("result") if isinstance(ai_result, dict) else None
    result_dict = result if isinstance(result, dict) else {}

    original_key = str(original_file_key or "").strip()
    aligned_key = str(
        result_dict.get("aligned_image_key")
        or result_dict.get("aligned_scan_image_key")
        or ""
    ).strip()

    if tenant_id is not None and aligned_key:
        expected_prefix = f"tenants/{int(tenant_id)}/"
        if not aligned_key.startswith(expected_prefix):
            aligned_key = ""

    selected_key = aligned_key or original_key
    size = result_dict.get("aligned_image_size") if aligned_key else None
    if not (
        isinstance(size, dict)
        and isinstance(size.get("width"), int)
        and isinstance(size.get("height"), int)
    ):
        size = None

    return {
        "scan_image_key": selected_key,
        "original_scan_image_key": original_key,
        "scan_image_is_aligned": bool(aligned_key),
        "scan_image_size": size,
    }


def build_omr_scan_image_payload(
    *,
    submission,
    expires_in: int = 21600,
) -> Dict[str, Any]:
    """Build presigned scan-image fields for API responses."""
    selection = select_omr_scan_image(
        submission_meta=getattr(submission, "meta", None),
        original_file_key=getattr(submission, "file_key", None),
        tenant_id=getattr(submission, "tenant_id", None),
    )

    scan_image_url = ""
    original_scan_image_url = ""
    selected_key = selection["scan_image_key"]
    original_key = selection["original_scan_image_key"]

    if selected_key:
        try:
            from apps.infrastructure.storage.r2 import generate_presigned_get_url

            scan_image_url = generate_presigned_get_url(
                key=selected_key,
                expires_in=expires_in,
            )
            if original_key and original_key != selected_key:
                original_scan_image_url = generate_presigned_get_url(
                    key=original_key,
                    expires_in=expires_in,
                )
        except Exception:
            scan_image_url = ""
            original_scan_image_url = ""

    return {
        "scan_image_url": scan_image_url,
        "original_scan_image_url": original_scan_image_url,
        "scan_image_is_aligned": selection["scan_image_is_aligned"],
        "scan_image_size": selection["scan_image_size"],
    }
