"""Cloudflare R2 object operation adapter.

Keep direct libs.r2_client.client usage out of domain code.
"""

from __future__ import annotations

from typing import Any


def head_object(key: str) -> tuple[bool, int]:
    from libs.r2_client.client import head_object as _head_object

    return _head_object(key)


def upload_fileobj(
    fileobj: Any,
    key: str,
    content_type: str = "application/octet-stream",
    bucket: str | None = None,
) -> None:
    from libs.r2_client.client import upload_fileobj as _upload_fileobj

    _upload_fileobj(fileobj, key, content_type=content_type, bucket=bucket)
