# apps/worker/run.py
from __future__ import annotations

import os
import sys
import time
import json
import signal
import logging
import requests

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from apps.worker.ai.pipelines.dispatcher import handle_ai_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AI-WORKER] %(message)s",
)
logger = logging.getLogger(__name__)

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
INTERNAL_WORKER_TOKEN = os.getenv("INTERNAL_WORKER_TOKEN", "long-random-secret")

POLL_INTERVAL_SEC = float(os.getenv("AI_WORKER_POLL_INTERVAL", "1.0"))

_running = True


def _shutdown(signum, frame):
    global _running
    logger.warning("Shutdown signal received (%s)", signum)
    _running = False


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


def fetch_job() -> AIJob | None:
    """
    API → Worker
    """
    url = f"{API_BASE_URL}/api/v1/internal/ai/job/next/"
    headers = {
        "X-Worker-Token": INTERNAL_WORKER_TOKEN,
    }

    resp = requests.get(url, headers=headers, timeout=10)

    if resp.status_code == 204:
        return None

    resp.raise_for_status()
    data = resp.json()
    return AIJob.from_dict(data)


def submit_result(result: AIResult) -> None:
    """
    Worker → API
    """
    url = f"{API_BASE_URL}/api/v1/internal/ai/result/"
    headers = {
        "X-Worker-Token": INTERNAL_WORKER_TOKEN,
        "Content-Type": "application/json",
    }

    resp = requests.post(
        url,
        json=result.to_dict(),
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()


def main():
    logger.info("AI Worker started")

    while _running:
        try:
            job = fetch_job()
            if job is None:
                time.sleep(POLL_INTERVAL_SEC)
                continue

            logger.info("Job received: id=%s type=%s", job.id, job.type)

            result = handle_ai_job(job)

            submit_result(result)

            logger.info(
                "Job finished: id=%s status=%s",
                job.id,
                result.status,
            )

        except Exception:
            logger.exception("Worker loop error")
            time.sleep(2.0)

    logger.info("AI Worker shutdown complete")


if __name__ == "__main__":
    sys.exit(main())
