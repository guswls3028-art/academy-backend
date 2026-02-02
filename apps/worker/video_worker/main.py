# PATH: apps/worker/video_worker/main.py
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys

from apps.worker.video_worker.config import load_config
from apps.worker.video_worker.http_client import VideoAPIClient
from apps.worker.video_worker.video.processor import process_video_job

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


def _self_stop_ec2() -> None:
    instance_id = os.environ.get("INSTANCE_ID")
    if not instance_id:
        return

    try:
        az = subprocess.check_output(
            ["curl", "-s", "http://169.254.169.254/latest/meta-data/placement/availability-zone"],
            text=True,
        ).strip()
        region = az[:-1]
    except Exception:
        return

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


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cfg = load_config()

    client = VideoAPIClient(
        base_url=cfg.API_BASE_URL,
        internal_worker_token=cfg.INTERNAL_WORKER_TOKEN,
        worker_id=cfg.WORKER_ID,
        timeout_seconds=cfg.HTTP_TIMEOUT_SECONDS,
    )

    logger.info("Video Worker started (single-run) api=%s", cfg.API_BASE_URL)

    try:
        job = client.fetch_next_job()

        if not job:
            logger.info("no job available")
            return 0

        if not isinstance(job, dict) or job.get("video_id") is None:
            raise RuntimeError("invalid job payload")

        process_video_job(job=job, cfg=cfg, client=client)
        return 0

    except Exception:
        logger.exception("fatal error")
        return 1

    finally:
        try:
            client.close()
        except Exception:
            pass
        _self_stop_ec2()
        logger.info("Video Worker shutdown complete")


if __name__ == "__main__":
    sys.exit(main())
