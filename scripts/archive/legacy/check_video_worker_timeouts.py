#!/usr/bin/env python
"""
확인: Video Worker 다운로드·presign 타임아웃 설정

장시간 영상(2h+) 인코딩 시 시간 초과 방지를 위한 설정 검증.
실행: python scripts/check_video_worker_timeouts.py
"""
from __future__ import annotations

import os
import sys

# academy 프로젝트 루트
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _float(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return float(default)


def _int(name: str, default: str) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return int(default)


def main() -> int:
    download = _float("DOWNLOAD_TIMEOUT_SECONDS", "600")
    presign = _int("VIDEO_WORKER_PRESIGN_GET_EXPIRES", "3600")

    print("Video Worker timeout settings (effective values):")
    print(f"  DOWNLOAD_TIMEOUT_SECONDS:     {download}s (env: {os.environ.get('DOWNLOAD_TIMEOUT_SECONDS', '(default)')})")
    print(f"  VIDEO_WORKER_PRESIGN_GET_EXPIRES: {presign}s (env: {os.environ.get('VIDEO_WORKER_PRESIGN_GET_EXPIRES', '(default)')})")
    print()
    print("권장: DOWNLOAD_TIMEOUT 300~600, PRESIGN 1800~3600 (2h+ 영상 대비)")
    if download < 300:
        print("  [WARN] DOWNLOAD_TIMEOUT_SECONDS < 300 — 장시간 다운로드 시 초과 가능")
    if presign < 1800:
        print("  [WARN] PRESIGN < 1800 — 대용량 다운로드 시 URL 만료 가능")
    return 0


if __name__ == "__main__":
    sys.exit(main())
