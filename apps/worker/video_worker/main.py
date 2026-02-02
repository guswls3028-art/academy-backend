# PATH: apps/worker/video_worker/main.py
from __future__ import annotations

import logging
import signal
import time
import os
import subprocess

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


def _shutdown_self():
    instance_id = os.environ.get("INSTANCE_ID")
    region = os.environ.get("AWS_REGION", "ap-northeast-2")

    if not instance_id:
        logger.warning("INSTANCE_ID not set; skip self shutdown")
        return

    logger.warning("idle limit reached; stopping instance %s", instance_id)
    subprocess.run(
        [
            "aws",
            "ec2",
            "stop-instances",
            "--instance-ids",
            instance_id,
            "--region",
            region,
        ],
        check=False,
    )


def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cfg = load_config()

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
    idle_count = 0
    IDLE_LIMIT = int(os.environ.get("VIDEO_WORKER_IDLE_LIMIT", "5"))

    try:
        while not _shutdown:
            try:
                job = client.fetch_next_job()

                if not job:
                    idle_count += 1
                    if idle_count >= IDLE_LIMIT:
                        _shutdown_self()
                        break

                    time.sleep(cfg.POLL_INTERVAL_SECONDS)
                    continue

                # reset idle counter on real job
                idle_count = 0

                if isinstance(job, dict) and "job" in job:
                    job = job.get("job")

                if not job or not isinstance(job, dict):
                    time.sleep(cfg.POLL_INTERVAL_SECONDS)
                    continue

                if job.get("video_id") is None:
                    logger.info("idle job payload=%s", job)
                    time.sleep(cfg.POLL_INTERVAL_SECONDS)
                    continue

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
