# PATH: apps/worker/ai_worker/run.py
from __future__ import annotations

import os
import sys
import logging
import requests
import time

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from apps.worker.ai_worker.ai.pipelines.dispatcher import handle_ai_job

# ==============================================================================
# AI WORKER – SINGLE RUN MODE (PRODUCTION FINAL)
#
# DESIGN PRINCIPLES (ENTERPRISE STANDARD):
# - Worker is NOT a daemon
# - Bounded polling within fixed lifetime window
# - No infinite loop
# - One execution = at most one job
# - Exit immediately after job 처리 or idle window expiration
#
# NOTE (OPS):
# - Process lifetime is capped to match EC2 billing granularity (default 60s)
# - Polling is allowed ONLY within this window
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AI-WORKER] %(message)s",
)
logger = logging.getLogger(__name__)

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
INTERNAL_WORKER_TOKEN = os.getenv("INTERNAL_WORKER_TOKEN", "long-random-secret")

# OPS: worker lifetime & polling
WORKER_MIN_LIFETIME = int(os.getenv("WORKER_MIN_LIFETIME", "60"))
POLL_INTERVAL_SECONDS = int(os.getenv("WORKER_POLL_INTERVAL", "5"))


def _headers() -> dict:
    return {
        "X-Worker-Token": INTERNAL_WORKER_TOKEN,
        "X-Worker-Id": os.getenv("HOSTNAME", "ai-worker"),
    }


def fetch_job() -> AIJob | None:
    """
    Ask API for the next AI job.
    API is the single source of truth.
    """
    url = f"{API_BASE_URL.rstrip('/')}/api/v1/internal/ai/job/next/"
    resp = requests.get(url, headers=_headers(), timeout=10)
    resp.raise_for_status()

    data = resp.json()
    job_data = data.get("job")
    if not job_data:
        return None

    return AIJob.from_dict(job_data)


def submit_result(*, result: AIResult, job: AIJob) -> None:
    """
    Submit job result back to API.
    """
    url = f"{API_BASE_URL.rstrip('/')}/api/v1/internal/ai/job/result/"
    headers = _headers()
    headers["Content-Type"] = "application/json"

    submission_id = None
    try:
        if job.source_id is not None and str(job.source_id).isdigit():
            submission_id = int(str(job.source_id))
    except Exception:
        submission_id = None

    payload = {
        "job_id": job.id,
        "submission_id": submission_id,
        "status": result.status,
        "result": result.result,
        "error": result.error,
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    resp.raise_for_status()


def main() -> int:
    """
    Single-run worker entrypoint with bounded polling.

    Flow:
    - Poll for job within WORKER_MIN_LIFETIME window
    - If job found → process once → exit
    - If no job until timeout → exit
    """
    start_ts = time.monotonic()
    deadline = start_ts + WORKER_MIN_LIFETIME

    logger.info(
        "AI Worker started (API_BASE_URL=%s, lifetime=%ss)",
        API_BASE_URL,
        WORKER_MIN_LIFETIME,
    )

    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                logger.info("Idle window expired (%ss). exiting.", WORKER_MIN_LIFETIME)
                return 0

            try:
                job = fetch_job()
            except Exception:
                logger.exception("fetch_job failed")
                return 1

            if job is None:
                sleep_sec = min(POLL_INTERVAL_SECONDS, max(0.0, deadline - now))
                if sleep_sec > 0:
                    time.sleep(sleep_sec)
                continue

            logger.info("Job received: id=%s type=%s", job.id, job.type)

            try:
                result = handle_ai_job(job)
                submit_result(result=result, job=job)
                logger.info("Job finished: id=%s status=%s", job.id, result.status)
                return 0
            except Exception:
                logger.exception("Job processing failed")
                return 1

    finally:
        elapsed = time.monotonic() - start_ts
        remain = WORKER_MIN_LIFETIME - elapsed

        if remain > 0:
            logger.info("Graceful sleep before shutdown: %.1fs", remain)
            time.sleep(remain)

        logger.info("AI Worker shutdown complete")


if __name__ == "__main__":
    sys.exit(main())
