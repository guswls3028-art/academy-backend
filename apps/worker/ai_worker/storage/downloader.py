# PATH: apps/worker/ai_worker/storage/downloader.py
"""URL 또는 R2 키에서 파일 다운로드 → 임시 경로 반환 (OCR, 세그멘테이션 등).

수명 관리 규칙:
- 모든 download_*_to_tmp는 prefix "ai-job-{job_id}-"로 mkdtemp 후 그 안에 파일 저장.
- 호출자는 작업 완료/실패 시 cleanup_tmp_for_path()로 부모 디렉터리 통째 제거.
- 정리 누락 시 워커 인스턴스 디스크 점진 누적 → 연쇄 실패. 반드시 try/finally.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


def cleanup_tmp_for_path(local_path: str | None) -> None:
    """download_*_to_tmp 가 만든 mkdtemp 부모 디렉터리를 통째 제거.

    local_path가 None/빈 문자열이거나 mkdtemp prefix("ai-job-")가 아니면 no-op.
    안전 가드: tmp 루트 외부는 절대 건드리지 않음.
    """
    if not local_path:
        return
    try:
        parent = Path(local_path).resolve().parent
        # mkdtemp prefix 검사 — 다른 디렉터리는 절대 삭제 금지
        if not parent.name.startswith("ai-job-"):
            return
        # 시스템 tmp 루트 하위인지 확인 (안전)
        try:
            tmp_root = Path(tempfile.gettempdir()).resolve()
            parent.relative_to(tmp_root)
        except (ValueError, OSError):
            logger.warning("cleanup_tmp_for_path skipped — not under tmp root: %s", parent)
            return
        shutil.rmtree(parent, ignore_errors=True)
    except Exception as e:
        logger.warning("cleanup_tmp_for_path failed: path=%s err=%s", local_path, e)


def download_to_tmp(*, download_url: str, job_id: str) -> str:
    """
    download_url에서 파일을 다운로드하여 임시 경로에 저장하고 경로를 반환.

    실패 시 본 함수 내부에서 mkdtemp를 정리하고 raise — 호출자가 받지 못한 경로는
    cleanup_tmp_for_path 대상에서 누락되므로 여기서 self-clean이 필수.

    Returns:
        str: 다운로드된 파일의 로컬 경로
    """
    parsed = urlparse(download_url)
    path_part = parsed.path or "input"
    ext = Path(path_part).suffix or ".bin"

    tmp_dir = tempfile.mkdtemp(prefix=f"ai-job-{job_id}-")
    try:
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
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def download_r2_key_to_tmp(*, r2_key: str, job_id: str) -> str:
    """
    R2 object key에서 파일을 다운로드하여 임시 경로에 저장하고 경로를 반환.

    R2 credentials are read from env vars (R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY).
    Bucket is determined from R2_STORAGE_BUCKET env var or Django settings.

    실패 시 본 함수 내부에서 mkdtemp를 정리하고 raise.

    Returns:
        str: 다운로드된 파일의 로컬 경로
    """
    ext = Path(r2_key).suffix or ".bin"

    tmp_dir = tempfile.mkdtemp(prefix=f"ai-job-{job_id}-")
    try:
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
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
