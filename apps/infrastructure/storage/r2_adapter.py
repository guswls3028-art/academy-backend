# PATH: apps/infrastructure/storage/r2_adapter.py
# R2(S3 호환) 객체 스토리지 어댑터 — Excel 파싱 워커용
# Django settings 또는 os.environ 사용 (워커 환경)

from __future__ import annotations

import os
from typing import Any


def _get_s3_client() -> Any:
    """R2/S3 클라이언트. Django 설정 또는 os.environ."""
    try:
        from django.conf import settings
        endpoint = getattr(settings, "R2_ENDPOINT", None) or os.environ.get("R2_ENDPOINT")
        access_key = getattr(settings, "R2_ACCESS_KEY", None) or os.environ.get("R2_ACCESS_KEY")
        secret_key = getattr(settings, "R2_SECRET_KEY", None) or os.environ.get("R2_SECRET_KEY")
    except Exception:
        endpoint = os.environ.get("R2_ENDPOINT")
        access_key = os.environ.get("R2_ACCESS_KEY")
        secret_key = os.environ.get("R2_SECRET_KEY")

    import boto3
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


class R2ObjectStorageAdapter:
    """R2(S3 호환) get/download/delete — ExcelParsingService용 (apps SSOT)."""

    def get_object(self, bucket: str, key: str) -> bytes:
        s3 = _get_s3_client()
        resp = s3.get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()

    def download_to_path(self, bucket: str, key: str, local_path: str) -> None:
        s3 = _get_s3_client()
        s3.download_file(Bucket=bucket, Key=key, Filename=local_path)

    def delete_object(self, bucket: str, key: str) -> None:
        s3 = _get_s3_client()
        s3.delete_object(Bucket=bucket, Key=key)
