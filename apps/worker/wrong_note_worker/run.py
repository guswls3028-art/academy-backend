# PATH: apps/worker/wrong_note_worker/run.py
from __future__ import annotations

import os
import time
import json
import math
import logging
import traceback
from typing import Any, Dict

import requests

from apps.worker.wrong_note_worker.config import load_config
from apps.worker.wrong_note_worker.client import APIClient
from apps.worker.wrong_note_worker.pdf_render import render_wrong_note_pdf


logger = logging.getLogger("wrong_note_worker")


def _sleep_backoff(base: float, attempt: int) -> None:
    # exponential backoff with cap
    sec = min(base * (2 ** max(attempt - 1, 0)), 30.0)
    time.sleep(sec)


def _upload_pdf(*, upload_url: str, pdf_bytes: bytes, timeout_seconds: float) -> None:
    # NOTE:
    # - 서버 presign이 Content-Type을 서명에 포함했으므로 동일하게 맞춘다.
    headers = {"Content-Type": "application/pdf"}
    r = requests.put(upload_url, data=pdf_bytes, headers=headers, timeout=timeout_seconds)
    r.raise_for_status()


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

    cfg = load_config()
    api = APIClient(
        api_base_url=cfg.API_BASE_URL,
        worker_token=cfg.WORKER_TOKEN,
        timeout_seconds=cfg.HTTP_TIMEOUT_SECONDS,
    )

    logger.info("WrongNoteWorker READY | worker_id=%s | api=%s", cfg.WORKER_ID, cfg.API_BASE_URL)

    while True:
        try:
            payload = api.get_next_job()
            if not payload.get("has_job"):
                time.sleep(cfg.POLL_INTERVAL_SECONDS)
                continue

            job = payload.get("job") or {}
            job_id = int(job.get("job_id"))

            logger.info("JOB picked | job_id=%s", job_id)

            # 1) fetch data
            data = api.get_job_data(job_id=job_id)
            filters = data.get("filters") or {}
            items = data.get("items") or []

            # 2) render pdf
            title = f"Wrong Notes (job_id={job_id})"
            pdf_bytes = render_wrong_note_pdf(
                title=title,
                filters=filters,
                items=items,
                max_items=cfg.PDF_MAX_ITEMS,
            )

            # 3) presign upload
            up = api.prepare_upload(job_id=job_id)
            upload_url = up.get("upload_url")
            file_key = up.get("file_key") or ""

            if not upload_url or not file_key:
                raise RuntimeError("prepare_upload returned empty upload_url/file_key")

            # 4) upload
            _upload_pdf(upload_url=upload_url, pdf_bytes=pdf_bytes, timeout_seconds=cfg.HTTP_TIMEOUT_SECONDS)

            # 5) complete
            api.complete(job_id=job_id, file_path=str(file_key), meta={
                "items_count": int(len(items)),
                "generated_at": time.time(),
            })

            logger.info("JOB done | job_id=%s | file_key=%s", job_id, file_key)

        except Exception as e:
            # best-effort: if we know job_id, report fail
            job_id = None
            try:
                # attempt to parse job_id from last logs/context is not reliable;
                # leave as None unless explicitly set above.
                pass
            except Exception:
                pass

            logger.error("Worker loop error: %s", e)
            logger.debug(traceback.format_exc())

            # If a job_id is known in this scope, report fail.
            # (In this implementation, job_id is local to the try-block after pick.
            #  If exception occurs after pick, it will still be available.)
            try:
                if "job_id" in locals() and locals().get("job_id") is not None:
                    api.fail(job_id=int(locals()["job_id"]), error_message=str(e))
            except Exception:
                logger.error("Failed to report job fail")

            time.sleep(cfg.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
