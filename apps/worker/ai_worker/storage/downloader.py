# PATH: apps/worker/ai_worker/storage/downloader.py
"""URL에서 파일 다운로드 → 임시 경로 반환 (OCR, 세그멘테이션 등)."""

from __future__ import annotations

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
