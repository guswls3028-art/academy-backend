# PATH: apps/worker/video_worker/main.py
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time

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
            timeout=2,
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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

    logger.info("Video Worker started (idle-window=120s, retry=1) api=%s", cfg.API_BASE_URL)

    idle_deadline = time.monotonic() + 120

    try:
        while not _shutdown:
            if time.monotonic() >= idle_deadline:
                logger.info("idle window expired (120s). exiting.")
                return 0

            try:
                job = client.fetch_next_job()
            except Exception:
                logger.exception("fetch_next_job failed")
                return 1

            if not job:
                time.sleep(5)
                continue

            if not isinstance(job, dict) or job.get("video_id") is None:
                logger.error("invalid job payload: %s", job)
                return 0

            # 🔥 최대 2회 시도
            for attempt in (1, 2):
                try:
                    logger.info(
                        "processing job video_id=%s attempt=%s",
                        job.get("video_id"),
                        attempt,
                    )
                    process_video_job(job=job, cfg=cfg, client=client)
                    return 0  # 성공 시 즉시 종료

                except Exception:
                    if attempt >= 2:
                        logger.exception("job failed after retry")
                        return 0  # 실패도 정상 종료
                    else:
                        logger.warning("job failed, retrying once...")
                        time.sleep(3)

            return 0

        logger.info("shutdown requested. exiting.")
        return 0

    except Exception:
        logger.exception("fatal worker crash")
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
