# PATH: apps/worker/wrong_note_worker/config.py
from __future__ import annotations

import os
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

    # PDF
    PDF_MAX_ITEMS: int


def load_config() -> Config:
    return Config(
        API_BASE_URL=_require("API_BASE_URL").rstrip("/"),
        WORKER_TOKEN=_require("WORKER_TOKEN"),
        WORKER_ID=os.environ.get("WORKER_ID", "wrong-note-worker-1"),

        POLL_INTERVAL_SECONDS=_float("POLL_INTERVAL_SECONDS", "2.0"),
        HTTP_TIMEOUT_SECONDS=_float("HTTP_TIMEOUT_SECONDS", "30.0"),
        RETRY_MAX_ATTEMPTS=_int("RETRY_MAX_ATTEMPTS", "5"),
        BACKOFF_BASE_SECONDS=_float("BACKOFF_BASE_SECONDS", "1.5"),

        PDF_MAX_ITEMS=_int("PDF_MAX_ITEMS", "200"),
    )
