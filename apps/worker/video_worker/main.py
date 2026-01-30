# PATH: apps/worker/video_worker/main.py

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

    # ✅ FIX: VideoAPIClient(cfg) 지원 + worker-id 헤더 포함은 http_client에서 처리
    client = VideoAPIClient(cfg)

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

                error_attempt = 0

                # ✅ 관측성(최소): job 시작 로그
                try:
                    logger.info("job received video_id=%s", job.get("video_id"))
                except Exception:
                    pass

                process_video_job(job=job, cfg=cfg, client=client)

            except Exception:
                logger.exception("worker loop error")
                error_attempt = min(error_attempt + 1, 10)
                backoff_sleep(error_attempt, cfg.BACKOFF_BASE_SECONDS, cfg.BACKOFF_CAP_SECONDS)

    finally:
        # ✅ FIX: close() 존재
        try:
            client.close()
        except Exception:
            pass
        logger.info("Video Worker shutdown complete")


if __name__ == "__main__":
    main()
