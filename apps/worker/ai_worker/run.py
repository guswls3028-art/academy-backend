# PATH: apps/worker/ai_worker/run.py
from __future__ import annotations

import os
import sys
import logging
import requests

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from apps.worker.ai_worker.ai.pipelines.dispatcher import handle_ai_job

# ==============================================================================
# AI WORKER – SINGLE RUN MODE (PRODUCTION FINAL)
#
# DESIGN PRINCIPLES (ENTERPRISE STANDARD):
# - Worker is NOT a daemon
# - No polling loop
# - No sleep / idle logic
# - No EC2 control
# - One execution = at most one job
# - Exit immediately when no job exists
#
# Execution / scaling / shutdown is handled OUTSIDE this process.
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AI-WORKER] %(message)s",
)
logger = logging.getLogger(__name__)

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
INTERNAL_WORKER_TOKEN = os.getenv("INTERNAL_WORKER_TOKEN", "long-random-secret")


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
    Single-run worker entrypoint.

    Flow:
    1. Ask API for one job
    2. If no job → exit immediately
    3. If job exists → process once → submit result → exit
    """
    logger.info("AI Worker started (API_BASE_URL=%s)", API_BASE_URL)

    try:
        job = fetch_job()

        if job is None:
            logger.info("No job found; exiting")
            return 0

        logger.info("Job received: id=%s type=%s", job.id, job.type)

        result = handle_ai_job(job)
        submit_result(result=result, job=job)

        logger.info("Job finished: id=%s status=%s", job.id, result.status)
        return 0

    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        logger.exception("Worker HTTP error (status=%s)", code)
        return 1

    except Exception:
        logger.exception("Worker fatal error")
        return 1

    finally:
        logger.info("AI Worker shutdown complete")


if __name__ == "__main__":
    sys.exit(main())
