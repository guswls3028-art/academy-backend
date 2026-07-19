"""Cloudflare R2 object operation adapter.

Keep direct libs.r2_client.client usage out of domain code.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from django.conf import settings


def _r2_client(*, retry_max_attempts: int | None = None):
    import boto3

    kwargs: dict[str, Any] = {
        "service_name": "s3",
        "region_name": "auto",
        "endpoint_url": settings.R2_ENDPOINT,
        "aws_access_key_id": settings.R2_ACCESS_KEY,
        "aws_secret_access_key": settings.R2_SECRET_KEY,
    }
    if retry_max_attempts is not None:
        from botocore.config import Config as BotoConfig

        kwargs["config"] = BotoConfig(retries={"max_attempts": retry_max_attempts, "mode": "standard"})
    return boto3.client(**kwargs)


def head_object(key: str) -> tuple[bool, int]:
    from libs.r2_client.client import head_object as _head_object

    return _head_object(key)


def head_storage_object_integrity(*, key: str) -> tuple[int, str] | None:
    """Return an immutable Storage object size/SHA metadata, or None when absent."""
    from botocore.exceptions import ClientError

    try:
        response = _r2_client().head_object(Bucket=settings.R2_STORAGE_BUCKET, Key=key)
        metadata = response.get("Metadata") or {}
        return int(response.get("ContentLength") or 0), str(metadata.get("sha256") or "").lower()
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def create_storage_download_url(
    *,
    key: str,
    filename: str,
    content_type: str,
    expires_in: int = 600,
) -> str:
    """Create a download-only URL for an object in the shared Storage bucket."""
    safe_filename = filename.replace('"', "")
    return _r2_client().generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": settings.R2_STORAGE_BUCKET,
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{safe_filename}"',
            "ResponseContentType": content_type,
        },
        ExpiresIn=expires_in,
    )


def upload_fileobj(
    fileobj: Any,
    key: str,
    content_type: str = "application/octet-stream",
    bucket: str | None = None,
) -> None:
    from libs.r2_client.client import upload_fileobj as _upload_fileobj

    _upload_fileobj(fileobj, key, content_type=content_type, bucket=bucket)


def r2_head_exists(*, bucket: str, key: str, retry_max_attempts: int | None = None) -> bool:
    try:
        _r2_client(retry_max_attempts=retry_max_attempts).head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def iter_r2_objects(
    *,
    bucket: str,
    prefix: str,
    max_keys: int | None = None,
) -> Iterator[dict[str, Any]]:
    paginator = _r2_client().get_paginator("list_objects_v2")
    paginate_kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
    if max_keys is not None:
        paginate_kwargs["MaxKeys"] = max_keys
    for page in paginator.paginate(**paginate_kwargs):
        yield from page.get("Contents") or []


def count_r2_objects_with_suffix(
    *,
    bucket: str,
    prefix: str,
    suffix: str,
    max_keys: int | None = None,
) -> int:
    count = 0
    try:
        for obj in iter_r2_objects(bucket=bucket, prefix=prefix, max_keys=max_keys):
            key = obj.get("Key") or ""
            if key.endswith(suffix):
                count += 1
    except Exception:
        return count
    return count


def delete_r2_objects(*, bucket: str, keys: Iterable[str]) -> int:
    deleted = 0
    batch: list[dict[str, str]] = []
    client = _r2_client()
    for key in keys:
        batch.append({"Key": key})
        if len(batch) >= 1000:
            client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
            deleted += len(batch)
            batch = []
    if batch:
        client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
        deleted += len(batch)
    return deleted


def delete_r2_prefix(*, bucket: str, prefix: str) -> int:
    total_deleted = 0
    continuation_token = None
    client = _r2_client()

    while True:
        list_kwargs: dict[str, Any] = {"Bucket": bucket, "MaxKeys": 1000, "Prefix": prefix}
        if continuation_token:
            list_kwargs["ContinuationToken"] = continuation_token
        resp = client.list_objects_v2(**list_kwargs)
        contents = resp.get("Contents") or []
        if contents:
            total_deleted += delete_r2_objects(bucket=bucket, keys=(obj["Key"] for obj in contents))
        if not resp.get("IsTruncated"):
            break
        continuation_token = resp.get("NextContinuationToken")

    return total_deleted
