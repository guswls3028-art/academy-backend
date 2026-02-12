# PATH: apps/worker/video_worker/main.py
from __future__ import annotations

import logging
import os
import signal
import sys
import time
import requests

from apps.worker.video_worker.config import load_config
from apps.worker.video_worker.http_client import VideoAPIClient
from apps.worker.video_worker.video.processor import process_video_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [VIDEO-WORKER] %(message)s",
)
logger = logging.getLogger("video_worker")

_shutdown = False

# OPS: idle window (seconds) — billing / autoscale 기준
WORKER_IDLE_WINDOW_SECONDS = int(os.getenv("VIDEO_WORKER_IDLE_WINDOW", "120"))

# 폴링 간격(초) - 최소 수정: 고정 1초 유지
POLL_INTERVAL_SECONDS = 1


# ------------------------------------------------------------------------------
# SIGNAL HANDLING
# ------------------------------------------------------------------------------
def _handle_signal(sig, frame):
    global _shutdown
    logger.warning("shutdown signal received sig=%s", sig)
    _shutdown = True


# ------------------------------------------------------------------------------
# EC2 SELF-STOP 제거됨 (SQS 기반 아키텍처에서는 불필요)
# ------------------------------------------------------------------------------
# ECS/Fargate 환경에서는 컨테이너가 자동으로 관리되므로 EC2 자동 종료 로직 불필요


# ------------------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------------------
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

    logger.info(
        "Video Worker started (continuous-polling, idle-window=%ss) api=%s",
        WORKER_IDLE_WINDOW_SECONDS,
        cfg.API_BASE_URL,
    )

    # 루프 시작 시간 기록
    started_at = time.monotonic()

    try:
        while not _shutdown:
            elapsed = time.monotonic() - started_at
            remaining = WORKER_IDLE_WINDOW_SECONDS - elapsed

            # ✅ 대기 시간 초과 시 루프 탈출 (이후 finally에서 인스턴스 종료)
            if remaining <= 0:
                logger.info(
                    "idle window expired (%ss). exiting.",
                    WORKER_IDLE_WINDOW_SECONDS,
                )
                break

            try:
                job = client.fetch_next_job()
            except Exception:
                logger.exception("fetch_next_job failed")
                return 1

            if not job:
                sleep_s = min(POLL_INTERVAL_SECONDS, max(0.0, remaining))
                time.sleep(sleep_s)
                continue

            if not isinstance(job, dict) or job.get("video_id") is None:
                logger.error("invalid job payload: %s", job)
                # 잘못된 데이터는 건너뛰고 계속 진행
                continue

            try:
                # 작업 처리
                process_video_job(job=job, cfg=cfg, client=client)
                
                # ✅ 작업을 성공적으로 처리했다면, 시작 시간을 현재로 갱신하여 
                # 다시 120초의 Idle Window를 부여합니다.
                logger.info("job processed. resetting idle timer.")
                started_at = time.monotonic()

            except Exception:
                logger.exception("job failed but handled")
                # 작업 실패 시에도 타이머를 리셋하여 다른 대기 중인 작업을 확인할 기회를 줍니다.
                started_at = time.monotonic()

        logger.info("shutdown requested or idle expired. exiting loop.")
        return 0

    except Exception:
        logger.exception("fatal worker crash")
        return 1

    finally:
        try:
            client.close()
        except Exception:
            pass

        logger.info("Video Worker shutdown complete")


if __name__ == "__main__":
    sys.exit(main())