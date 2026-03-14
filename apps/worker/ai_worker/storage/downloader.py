# PATH: apps/worker/ai_worker/storage/downloader.py
"""URL 또는 R2 키에서 파일 다운로드 → 임시 경로 반환 (OCR, 세그멘테이션 등)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests


def download_to_tmp(*, download_url: str, job_id: str) -> str:
    """
    download_url에서 파일을 다운로드하여 임시 경로에 저장하고 경로를 반환.

    Returns:
        str: 다운로드된 파일의 로컬 경로
    """
    parsed = urlparse(download_url)
    path_part = parsed.path or "input"
    ext = Path(path_part).suffix or ".bin"

    tmp_dir = tempfile.mkdtemp(prefix=f"ai-job-{job_id}-")
    local_path = Path(tmp_dir) / f"input{ext}"

    resp = requests.get(download_url, stream=True, timeout=60)
    resp.raise_for_status()

    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    if local_path.stat().st_size <= 0:
        raise RuntimeError("downloaded file is empty")

    return str(local_path.resolve())


def download_r2_key_to_tmp(*, r2_key: str, job_id: str) -> str:
    """
    R2 object key에서 파일을 다운로드하여 임시 경로에 저장하고 경로를 반환.

    R2 credentials are read from env vars (R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY).
    Bucket is determined from R2_STORAGE_BUCKET env var or Django settings.

    Returns:
        str: 다운로드된 파일의 로컬 경로
    """
    ext = Path(r2_key).suffix or ".bin"

    tmp_dir = tempfile.mkdtemp(prefix=f"ai-job-{job_id}-")
    local_path = str(Path(tmp_dir) / f"input{ext}")

    from apps.infrastructure.storage.r2_adapter import R2ObjectStorageAdapter

    # Resolve bucket name: Django settings first, then env var, then default
    try:
        from django.conf import settings as django_settings
        bucket = getattr(django_settings, "R2_STORAGE_BUCKET", None)
    except Exception:
        bucket = None
    if not bucket:
        bucket = os.environ.get("R2_STORAGE_BUCKET", "academy-storage")

    storage = R2ObjectStorageAdapter()
    storage.download_to_path(bucket, r2_key, local_path)

    if Path(local_path).stat().st_size <= 0:
        raise RuntimeError("downloaded file from R2 is empty")

    return local_path
