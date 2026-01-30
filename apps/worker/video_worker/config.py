from __future__ import annotations

import os
import sys
from dataclasses import dataclass


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env: {name}")
    return v


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


@dataclass(frozen=True)
class Config:
    # API
    API_BASE_URL: str
    WORKER_TOKEN: str
    WORKER_ID: str

    # Polling / retry
    POLL_INTERVAL_SECONDS: float
    HTTP_TIMEOUT_SECONDS: float
    RETRY_MAX_ATTEMPTS: int
    BACKOFF_BASE_SECONDS: float
    BACKOFF_CAP_SECONDS: float

    # Temp
    TEMP_DIR: str

    # Locking (Idempotency)
    LOCK_DIR: str
    LOCK_STALE_SECONDS: int

    # Heartbeat
    HEARTBEAT_INTERVAL_SECONDS: int

    # ffmpeg / ffprobe
    FFMPEG_BIN: str
    FFPROBE_BIN: str
    FFPROBE_TIMEOUT_SECONDS: int
    FFMPEG_TIMEOUT_SECONDS: int

    # HLS / thumb
    HLS_TIME_SECONDS: int
    THUMBNAIL_AT_SECONDS: float

    # Validation
    MIN_SEGMENTS_PER_VARIANT: int

    # R2 (S3 compatible)
    R2_BUCKET: str
    R2_PREFIX: str
    R2_ENDPOINT_URL: str
    R2_ACCESS_KEY: str
    R2_SECRET_KEY: str
    R2_REGION: str
    UPLOAD_MAX_CONCURRENCY: int

    # download tuning
    DOWNLOAD_TIMEOUT_SECONDS: float
    DOWNLOAD_CHUNK_BYTES: int


def load_config() -> Config:
    try:
        return Config(
            API_BASE_URL=_require("API_BASE_URL").rstrip("/"),
            WORKER_TOKEN=_require("INTERNAL_WORKER_TOKEN"),
            WORKER_ID=os.environ.get("WORKER_ID", "video-worker-1"),

            POLL_INTERVAL_SECONDS=_float("VIDEO_WORKER_POLL_INTERVAL", "1.0"),
            HTTP_TIMEOUT_SECONDS=_float("VIDEO_WORKER_HTTP_TIMEOUT", "10.0"),
            RETRY_MAX_ATTEMPTS=_int("VIDEO_WORKER_RETRY_MAX", "6"),
            BACKOFF_BASE_SECONDS=_float("VIDEO_WORKER_BACKOFF_BASE", "0.5"),
            BACKOFF_CAP_SECONDS=_float("VIDEO_WORKER_BACKOFF_CAP", "10.0"),

            TEMP_DIR=os.environ.get("VIDEO_WORKER_TEMP_DIR", "/tmp/video-worker"),

            # Idempotency lock
            LOCK_DIR=os.environ.get("VIDEO_WORKER_LOCK_DIR", "/tmp/video-worker-locks"),
            LOCK_STALE_SECONDS=_int("VIDEO_WORKER_LOCK_STALE_SECONDS", "3600"),

            # Heartbeat
            HEARTBEAT_INTERVAL_SECONDS=_int("VIDEO_WORKER_HEARTBEAT_INTERVAL", "20"),

            FFMPEG_BIN=os.environ.get("FFMPEG_BIN", "ffmpeg"),
            FFPROBE_BIN=os.environ.get("FFPROBE_BIN", "ffprobe"),
            FFPROBE_TIMEOUT_SECONDS=_int("FFPROBE_TIMEOUT_SECONDS", "60"),
            FFMPEG_TIMEOUT_SECONDS=_int("FFMPEG_TIMEOUT_SECONDS", "3600"),  # 1h default

            HLS_TIME_SECONDS=_int("HLS_TIME_SECONDS", "4"),
            THUMBNAIL_AT_SECONDS=_float("THUMBNAIL_AT_SECONDS", "1.0"),

            MIN_SEGMENTS_PER_VARIANT=_int("MIN_SEGMENTS_PER_VARIANT", "3"),

            R2_BUCKET=_require("R2_BUCKET"),
            R2_PREFIX=os.environ.get("R2_PREFIX", "media/hls/videos"),
            R2_ENDPOINT_URL=_require("R2_ENDPOINT_URL"),
            R2_ACCESS_KEY=_require("R2_ACCESS_KEY"),
            R2_SECRET_KEY=_require("R2_SECRET_KEY"),
            R2_REGION=os.environ.get("R2_REGION", "auto"),
            UPLOAD_MAX_CONCURRENCY=_int("UPLOAD_MAX_CONCURRENCY", "8"),

            DOWNLOAD_TIMEOUT_SECONDS=_float("DOWNLOAD_TIMEOUT_SECONDS", "30.0"),
            DOWNLOAD_CHUNK_BYTES=_int("DOWNLOAD_CHUNK_BYTES", str(1024 * 1024)),
        )
    except Exception as e:
        print(f"[fatal] config error: {e}", file=sys.stderr)
        sys.exit(1)
