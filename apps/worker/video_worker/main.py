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
# EC2 SELF-STOP (AI WORKER와 동일 패턴)
# ------------------------------------------------------------------------------
def _self_stop_ec2() -> None:
    """
    Best-effort EC2 self stop.

    - Uses IMDSv2
    - Requires IAM role permission: ec2:StopInstances
    - Failure must NEVER affect worker exit code
    """
    try:
        import boto3

        # IMDSv2 token
        token = requests.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            timeout=2,
        ).text

        headers = {"X-aws-ec2-metadata-token": token}

        instance_id = requests.get(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers=headers,
            timeout=2,
        ).text

        region = requests.get(
            "http://169.254.169.254/latest/meta-data/placement/region",
            headers=headers,
            timeout=2,
        ).text

        ec2 = boto3.client("ec2", region_name=region)
        ec2.stop_instances(InstanceIds=[instance_id])

        logger.info("EC2 self-stop requested (instance_id=%s)", instance_id)

    except Exception as e:
        # 절대 worker 실패 원인이 되면 안 됨
        logger.exception("EC2 self-stop failed (ignored): %s", e)


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
        "Video Worker started (single-run, idle-window=%ss) api=%s",
        WORKER_IDLE_WINDOW_SECONDS,
        cfg.API_BASE_URL,
    )

    started_at = time.monotonic()

    try:
        while not _shutdown:
            elapsed = time.monotonic() - started_at
            remaining = WORKER_IDLE_WINDOW_SECONDS - elapsed

            # ✅ 정확히 120초 폴링 보장: 남은 시간이 0 이하면 종료
            if remaining <= 0:
                logger.info(
                    "idle window expired (%ss). exiting.",
                    WORKER_IDLE_WINDOW_SECONDS,
                )
                return 0

            try:
                job = client.fetch_next_job()
            except Exception:
                # fetch 실패 = 워커 장애
                logger.exception("fetch_next_job failed")
                return 1

            if not job:
                # ✅ 남은 시간보다 오래 자지 않게 조정 (정확히 120초 안에 종료)
                sleep_s = min(POLL_INTERVAL_SECONDS, max(0.0, remaining))
                time.sleep(sleep_s)
                continue

            if not isinstance(job, dict) or job.get("video_id") is None:
                logger.error("invalid job payload: %s", job)
                return 0  # 논리 오류 → 재시작 불필요

            try:
                process_video_job(job=job, cfg=cfg, client=client)
            except Exception:
                # process_video_job 내부에서 notify_fail 완료됨
                logger.exception("job failed but handled")
                return 0  # 실패도 정상 종료

            # job 하나 처리하면 즉시 종료
            return 0

        logger.info("shutdown requested. exiting.")
        return 0

    except Exception:
        # 여기 도달 = 프로세스 붕괴
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
