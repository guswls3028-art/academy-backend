# PATH: apps/worker/ai_worker/run.py
from __future__ import annotations

import os
import sys
import time
import signal
import logging
import random
import requests

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from apps.worker.ai_worker.ai.pipelines.dispatcher import handle_ai_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AI-WORKER] %(message)s",
)
logger = logging.getLogger(__name__)

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
INTERNAL_WORKER_TOKEN = os.getenv("INTERNAL_WORKER_TOKEN", "long-random-secret")
POLL_INTERVAL_SEC = float(os.getenv("AI_WORKER_POLL_INTERVAL", "1.0"))
MAX_BACKOFF = float(os.getenv("AI_WORKER_MAX_BACKOFF", "30.0"))

_running = True


def _shutdown(signum, frame):
    global _running
    logger.warning("Shutdown signal received (%s)", signum)
    _running = False


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


def _headers() -> dict:
    return {
        "X-Worker-Token": INTERNAL_WORKER_TOKEN,
        "X-Worker-Id": os.getenv("HOSTNAME", "ai-worker"),
    }


def fetch_job() -> AIJob | None:
    url = f"{API_BASE_URL.rstrip('/')}/api/v1/internal/ai/job/next/"
    resp = requests.get(url, headers=_headers(), timeout=10)
    resp.raise_for_status()

    data = resp.json()
    job_data = data.get("job")
    if not job_data:
        return None

    return AIJob.from_dict(job_data)


def submit_result(*, result: AIResult, job: AIJob) -> None:
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


def main():
    logger.info("AI Worker started (API_BASE_URL=%s)", API_BASE_URL)

    backoff = 0.0

    while _running:
        try:
            job = fetch_job()
            if job is None:
                time.sleep(POLL_INTERVAL_SEC)
                backoff = 0.0
                continue

            logger.info("Job received: id=%s type=%s", job.id, job.type)

            result = handle_ai_job(job)

            submit_result(result=result, job=job)

            logger.info("Job finished: id=%s status=%s", job.id, result.status)
            backoff = 0.0

        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            logger.exception("Worker loop HTTP error (status=%s)", code)
            backoff = min(MAX_BACKOFF, backoff * 2.0 + 1.0) if backoff > 0 else 1.0
            time.sleep(backoff + random.random())

        except Exception:
            logger.exception("Worker loop error")
            backoff = min(MAX_BACKOFF, backoff * 2.0 + 1.0) if backoff > 0 else 1.0
            time.sleep(backoff + random.random())

    logger.info("AI Worker shutdown complete")


if __name__ == "__main__":
    sys.exit(main())
