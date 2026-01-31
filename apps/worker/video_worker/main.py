from __future__ import annotations

import logging
import signal
import time

from apps.worker.video_worker.config import load_config
from apps.worker.video_worker.http_client import VideoAPIClient
from apps.worker.video_worker.video.processor import process_video_job
from apps.worker.video_worker.utils import backoff_sleep

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [VIDEO-WORKER] %(message)s",
)
logger = logging.getLogger("video_worker")

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    logger.warning("shutdown signal received sig=%s", sig)
    _shutdown = True


def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cfg = load_config()

    # ✅ FIX: 생성자 시그니처 명확히 맞춤 (원본 구조 유지)
    client = VideoAPIClient(
        base_url=cfg.API_BASE_URL,
        worker_token=cfg.WORKER_TOKEN,
        worker_id=cfg.WORKER_ID,
        timeout_seconds=int(cfg.HTTP_TIMEOUT_SECONDS),
    )

    logger.info(
        "Video Worker started worker_id=%s api=%s poll=%ss",
        cfg.WORKER_ID,
        cfg.API_BASE_URL,
        cfg.POLL_INTERVAL_SECONDS,
    )

    error_attempt = 0

    try:
        while not _shutdown:
            try:
                job = client.fetch_next_job()
                if not job:
                    time.sleep(cfg.POLL_INTERVAL_SECONDS)
                    continue

                # ↓↓↓ PATCH START ↓↓↓
                if isinstance(job, dict) and "job" in job:  # MODIFIED
                    job = job.get("job")  # MODIFIED

                if not job or not isinstance(job, dict):  # MODIFIED
                    time.sleep(cfg.POLL_INTERVAL_SECONDS)  # MODIFIED
                    continue  # MODIFIED

                if "video_id" not in job:  # MODIFIED
                    logger.warning("job missing video_id payload=%s", job)  # MODIFIED
                    time.sleep(cfg.POLL_INTERVAL_SECONDS)  # MODIFIED
                    continue  # MODIFIED
                # ↑↑↑ PATCH END ↑↑↑

                error_attempt = 0

                logger.info("job received video_id=%s", job.get("video_id"))

                process_video_job(job=job, cfg=cfg, client=client)

            except Exception:
                logger.exception("worker loop error")
                error_attempt = min(error_attempt + 1, 10)
                backoff_sleep(
                    error_attempt,
                    cfg.BACKOFF_BASE_SECONDS,
                    cfg.BACKOFF_CAP_SECONDS,
                )

    finally:
        try:
            client.close()
        except Exception:
            pass
        logger.info("Video Worker shutdown complete")


if __name__ == "__main__":
    main()
