# ==============================================================================
# PATH: apps/infrastructure/storage/r2.py
#
# PURPOSE:
# - API 서버 전용 R2(S3) 접근 레이어
# - AI/Video: R2_AI_BUCKET, R2_VIDEO_BUCKET
# - Storage(인벤토리): R2_STORAGE_BUCKET — upload / copy / delete / presign
# ==============================================================================

from __future__ import annotations

from typing import Callable

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


def _excel_bucket():
    return getattr(settings, "R2_EXCEL_BUCKET", "academy-excel")


def upload_fileobj_to_r2_excel(
    *,
    fileobj,
    key: str,
    content_type: str | None = None,
) -> None:
    """
    Django UploadedFile -> R2 엑셀 버킷 업로드.
    워커 EXCEL_PARSING job이 동일 버킷에서 다운로드하므로 R2_EXCEL_BUCKET 일치 필요.
    """
    s3 = _get_s3_client()
    s3.upload_fileobj(
        Fileobj=fileobj,
        Bucket=_excel_bucket(),
        Key=key,
        ExtraArgs={
            "ContentType": content_type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        },
    )


def generate_presigned_get_url_excel(
    *,
    key: str,
    expires_in: int = 3600,
) -> str:
    """R2 엑셀 버킷 presigned GET URL (다운로드용)."""
    s3 = _get_s3_client()
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": _excel_bucket(),
            "Key": key,
        },
        ExpiresIn=expires_in,
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


# ---------------------------------------------------------------------
# Video 버킷 (R2_VIDEO_BUCKET) — raw/HLS 삭제
# ---------------------------------------------------------------------

def _video_bucket():
    return getattr(settings, "R2_VIDEO_BUCKET", "academy-video")


def delete_object_r2_video(*, key: str) -> None:
    """R2 Video 버킷에서 객체 1건 삭제 (raw 등)."""
    s3 = _get_s3_client()
    s3.delete_object(Bucket=_video_bucket(), Key=key)


def delete_prefix_r2_video(
    *,
    prefix: str,
    on_batch_deleted: Callable[[int], None] | None = None,
) -> int:
    """
    R2 Video 버킷에서 prefix 아래 모든 객체 일괄 삭제 (HLS 등).
    delete_objects 최대 1000건씩 배치 처리.
    on_batch_deleted(total_deleted): 배치 삭제 직후 호출 (visibility 연장 등, 장시간 삭제 대비).
    Returns:
        삭제한 객체 수.
    """
    s3 = _get_s3_client()
    bucket = _video_bucket()
    total_deleted = 0
    continuation_token = None
    list_page = 1000
    delete_batch = 1000

    while True:
        list_kw = {"Bucket": bucket, "MaxKeys": list_page, "Prefix": prefix}
        if continuation_token:
            list_kw["ContinuationToken"] = continuation_token
        resp = s3.list_objects_v2(**list_kw)
        contents = resp.get("Contents") or []
        if not contents:
            if not resp.get("IsTruncated"):
                break
            continuation_token = resp.get("NextContinuationToken")
            continue
        keys = [{"Key": obj["Key"]} for obj in contents]
        for i in range(0, len(keys), delete_batch):
            batch = keys[i : i + delete_batch]
            s3.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
            total_deleted += len(batch)
            if callable(on_batch_deleted):
                on_batch_deleted(total_deleted)
        if not resp.get("IsTruncated"):
            break
        continuation_token = resp.get("NextContinuationToken")

    return total_deleted


# ---------------------------------------------------------------------
# Admin 버킷 (R2_ADMIN_BUCKET) — 테넌트 로고 등
# ---------------------------------------------------------------------

def _admin_bucket():
    return getattr(settings, "R2_ADMIN_BUCKET", "academy-admin")


def upload_fileobj_to_r2_admin(
    *,
    fileobj,
    key: str,
    content_type: str | None = None,
) -> None:
    """Django UploadedFile -> R2 Admin 버킷 업로드 (테넌트 로고 등)."""
    s3 = _get_s3_client()
    s3.upload_fileobj(
        Fileobj=fileobj,
        Bucket=_admin_bucket(),
        Key=key,
        ExtraArgs={
            "ContentType": content_type or "application/octet-stream"
        },
    )


def get_admin_object_public_url(*, key: str) -> str | None:
    """
    Admin 버킷 객체의 공개 URL.
    R2_ADMIN_PUBLIC_BASE_URL이 설정된 경우에만 반환 (없으면 None → presigned 사용).
    """
    base = getattr(settings, "R2_ADMIN_PUBLIC_BASE_URL", "") or ""
    base = (base or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/{key}" if key else base


def generate_presigned_get_url_admin(
    *,
    key: str,
    expires_in: int = 3600,
) -> str:
    """R2 Admin 버킷 presigned GET URL (공개 URL 미설정 시 로고 등 조회용)."""
    s3 = _get_s3_client()
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": _admin_bucket(),
            "Key": key,
        },
        ExpiresIn=expires_in,
    )
