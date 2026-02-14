# ==============================================================================
# PATH: apps/infrastructure/storage/r2.py
#
# PURPOSE:
# - API 서버 전용 R2(S3) 접근 레이어
# - AI/Video: R2_AI_BUCKET, R2_VIDEO_BUCKET
# - Storage(인벤토리): R2_STORAGE_BUCKET — upload / copy / delete / presign
# ==============================================================================

from __future__ import annotations

import boto3
from django.conf import settings


def _get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT,
        aws_access_key_id=settings.R2_ACCESS_KEY,
        aws_secret_access_key=settings.R2_SECRET_KEY,
        region_name="auto",
    )


# ---------------------------------------------------------------------
# Upload (AI 버킷 — 기존)
# ---------------------------------------------------------------------

def upload_fileobj_to_r2(
    *,
    fileobj,
    key: str,
    content_type: str | None = None,
) -> None:
    """
    Django UploadedFile -> R2 업로드 (AI 버킷)
    """
    s3 = _get_s3_client()
    s3.upload_fileobj(
        Fileobj=fileobj,
        Bucket=settings.R2_AI_BUCKET,
        Key=key,
        ExtraArgs={
            "ContentType": content_type or "application/octet-stream"
        },
    )


def generate_presigned_get_url(
    *,
    key: str,
    expires_in: int = 3600,
) -> str:
    """
    R2 object presigned GET URL (AI 버킷)
    """
    s3 = _get_s3_client()
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": settings.R2_AI_BUCKET,
            "Key": key,
        },
        ExpiresIn=expires_in,
    )


# ---------------------------------------------------------------------
# Storage 버킷 (인벤토리) — R2_STORAGE_BUCKET
# ---------------------------------------------------------------------

def _storage_bucket():
    return getattr(settings, "R2_STORAGE_BUCKET", "academy-storage")


def upload_fileobj_to_r2_storage(
    *,
    fileobj,
    key: str,
    content_type: str | None = None,
) -> None:
    """Django UploadedFile -> R2 Storage 버킷 업로드."""
    s3 = _get_s3_client()
    s3.upload_fileobj(
        Fileobj=fileobj,
        Bucket=_storage_bucket(),
        Key=key,
        ExtraArgs={
            "ContentType": content_type or "application/octet-stream"
        },
    )


def generate_presigned_get_url_storage(
    *,
    key: str,
    expires_in: int = 3600,
) -> str:
    """R2 Storage 버킷 presigned GET URL."""
    s3 = _get_s3_client()
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": _storage_bucket(),
            "Key": key,
        },
        ExpiresIn=expires_in,
    )


def copy_object_r2_storage(*, source_key: str, dest_key: str) -> None:
    """
    R2 내부 복사 (Copy & Delete 이동 시 사용, 대역폭 비용 없음).
    같은 버킷 내 copy_object.
    """
    s3 = _get_s3_client()
    bucket = _storage_bucket()
    s3.copy_object(
        CopySource={"Bucket": bucket, "Key": source_key},
        Bucket=bucket,
        Key=dest_key,
    )


def delete_object_r2_storage(*, key: str) -> None:
    """R2 Storage 버킷에서 객체 삭제. 이동 성공 확인 후에만 호출."""
    s3 = _get_s3_client()
    s3.delete_object(Bucket=_storage_bucket(), Key=key)


def head_object_r2_storage(*, key: str) -> tuple[bool, int]:
    """객체 존재 여부 및 크기(bytes)."""
    from botocore.exceptions import ClientError
    s3 = _get_s3_client()
    try:
        resp = s3.head_object(Bucket=_storage_bucket(), Key=key)
        return True, int(resp.get("ContentLength") or 0)
    except ClientError as err:
        code = (err.response.get("Error") or {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False, 0
        raise
