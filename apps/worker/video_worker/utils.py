from __future__ import annotations

import logging
import random
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger("video_worker")


@contextmanager
def temp_workdir(base_dir: str, prefix: str):
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=base_dir))
    try:
        yield path
    finally:
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            logger.warning("Failed to cleanup temp dir: %s", path)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def backoff_sleep(attempt: int, base: float, cap: float) -> None:
    raw = min(cap, base * (2 ** attempt))
    jitter = random.uniform(0.5, 1.5)
    time.sleep(raw * jitter)


def trim_tail(s: str, limit: int = 2000) -> str:
    if not s:
        return ""
    return s[-limit:] if len(s) > limit else s


def guess_content_type(name: str) -> str:
    n = name.lower()
    if n.endswith(".m3u8"):
        return "application/vnd.apple.mpegurl"
    if n.endswith(".ts"):
        return "video/MP2T"
    if n.endswith(".mp4"):
        return "video/mp4"
    if n.endswith(".jpg") or n.endswith(".jpeg"):
        return "image/jpeg"
    if n.endswith(".png"):
        return "image/png"
    if n.endswith(".json"):
        return "application/json"
    return "application/octet-stream"


def cache_control_for_object(name: str) -> str:
    """
    R2 Cache-Control 전략 (요구사항 반영)

    - HLS playlist (.m3u8): 서명 정책/쿠키 기반 접근을 전제로 "no-cache"
      (플레이리스트는 재생 정책/토큰 갱신 영향 받음)
    - Segment (.ts): immutable (콘텐츠 주소가 prefix/video_id 고정이라도,
      세그먼트는 VOD 생성 후 변경되지 않는 것이 정상)
    - Thumbnail: 7d 캐시
    """
    n = name.lower()
    if n.endswith(".m3u8"):
        return "no-cache"
    if n.endswith(".ts"):
        return "public, max-age=31536000, immutable"
    if n.endswith(".jpg") or n.endswith(".jpeg") or n.endswith(".png"):
        return "public, max-age=604800"
    return "public, max-age=3600"
