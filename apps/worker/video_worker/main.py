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
    """
    단일 진실:
    - worker는 자기 자신만 종료
    - INSTANCE_ID는 systemd Environment로 주입
    - region은 EC2 metadata에서 계산
    """
    instance_id = os.environ.get("INSTANCE_ID")
    if not instance_id:
        logger.warning("INSTANCE_ID not set; skip self shutdown")
        return

    try:
        az = subprocess.check_output(
            ["curl", "-s", "http://169.254.169.254/latest/meta-data/placement/availability-zone"],
            text=True,
        ).strip()
        region = az[:-1]  # ap-northeast-2a → ap-northeast-2
    except Exception as e:
        logger.error("failed to resolve region from metadata: %s", e)
        return

    logger.warning("no job available; stopping instance %s", instance_id)

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
        "Video Worker started (single-run) worker_id=%s api=%s",
        cfg.WORKER_ID,
        cfg.API_BASE_URL,
    )

    error_attempt = 0

    try:
        while not _shutdown:
            try:
                job = client.fetch_next_job()

                # ✅ 선택지 A 핵심:
                # job 없음(204) → 즉시 self-stop + 종료
                if not job:
                    _shutdown_self()
                    break

                if isinstance(job, dict) and "job" in job:
                    job = job.get("job")

                if not job or not isinstance(job, dict):
                    _shutdown_self()
                    break

                if job.get("video_id") is None:
                    _shutdown_self()
                    break

                error_attempt = 0

                logger.info("job received video_id=%s", job.get("video_id"))

                process_video_job(job=job, cfg=cfg, client=client)

                # 단일 작업 완료 후 종료
                _shutdown_self()
                break

            except Exception:
                logger.exception("worker error")
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
