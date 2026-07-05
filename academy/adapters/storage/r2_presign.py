"""Cloudflare R2 presigned URL adapter.

Domain code imports this module instead of libs.r2_client.presign so R2/S3
details stay behind the storage adapter boundary.
"""

from __future__ import annotations

from typing import Any


def _presign():
    from libs.r2_client import presign

    return presign


def create_presigned_put_url(
    key: str,
    content_type: str,
    expires_in: int | None = None,
) -> str:
    kwargs: dict[str, Any] = {"key": key, "content_type": content_type}
    if expires_in is not None:
        kwargs["expires_in"] = expires_in
    return _presign().create_presigned_put_url(**kwargs)


def create_presigned_get_url(
    key: str,
    expires_in: int | None = None,
    bucket: str | None = None,
) -> str:
    kwargs: dict[str, Any] = {"key": key}
    if expires_in is not None:
        kwargs["expires_in"] = expires_in
    if bucket is not None:
        kwargs["bucket"] = bucket
    return _presign().create_presigned_get_url(**kwargs)


def create_multipart_upload(key: str, content_type: str) -> str:
    return _presign().create_multipart_upload(key=key, content_type=content_type)


def create_presigned_upload_part_url(
    key: str,
    upload_id: str,
    part_number: int,
    expires_in: int | None = None,
) -> str:
    kwargs: dict[str, Any] = {"key": key, "upload_id": upload_id, "part_number": part_number}
    if expires_in is not None:
        kwargs["expires_in"] = expires_in
    return _presign().create_presigned_upload_part_url(**kwargs)


def complete_multipart_upload(key: str, upload_id: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    return _presign().complete_multipart_upload(key=key, upload_id=upload_id, parts=parts)


def abort_multipart_upload(key: str, upload_id: str) -> None:
    _presign().abort_multipart_upload(key=key, upload_id=upload_id)
