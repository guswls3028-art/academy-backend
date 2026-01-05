# apps/worker/run.py
from __future__ import annotations

import os
import sys
import time
import signal
import logging
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
    GET /api/v1/internal/ai/job/next/
    response: { "job": {...} | null }
    """
    url = f"{API_BASE_URL}/api/v1/internal/ai/job/next/"
    headers = {"X-Worker-Token": INTERNAL_WORKER_TOKEN}

    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()

    data = resp.json()
    job_data = data.get("job")
    if not job_data:
        return None

    return AIJob.from_dict(job_data)


def submit_result(result: AIResult, submission_id: int) -> None:
    """
    Worker → API
    POST /api/v1/internal/ai/job/result/
    """
    url = f"{API_BASE_URL}/api/v1/internal/ai/job/result/"
    headers = {
        "X-Worker-Token": INTERNAL_WORKER_TOKEN,
        "Content-Type": "application/json",
    }

    # ✅ 구조 고정 (중요)
    payload = {
        "submission_id": submission_id,
        "status": result.status,
        "result": result.result,
        "error": result.error,
    }

    resp = requests.post(
        url,
        json=payload,
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

            # 🔥 AI 처리
            result = handle_ai_job(job)

            # 🔥 결과 전송 (submission_id = job.source_id)
            submit_result(result, int(job.source_id))

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
