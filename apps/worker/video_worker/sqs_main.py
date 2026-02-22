"""
Video Worker - SQS 기반 메인 엔트리포인트

기존 HTTP polling 방식에서 SQS Long Polling으로 전환
"""

from __future__ import annotations

import os
import sys

# Django 설정 필수 — VideoRepository 등이 ORM 사용. 미설정 시 worker 전용 설정으로 고정
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.worker")
import django
django.setup()

import json
import logging
import signal
import subprocess
import threading
import time
import uuid
from typing import Optional

from apps.worker.video_worker.config import load_config
from libs.queue import QueueUnavailableError
from src.infrastructure.video import VideoSQSAdapter
from src.infrastructure.video.processor import process_video
from src.infrastructure.cache.redis_idempotency_adapter import RedisIdempotencyAdapter
from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter
from apps.support.video.redis_status_cache import set_video_heartbeat, delete_video_heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [VIDEO-WORKER-SQS] %(message)s",
)
logger = logging.getLogger("video_worker_sqs")

_shutdown = False
spot_termination_event = threading.Event()  # EC2 Spot / ASG scale-in drain (metadata or SIGTERM)
_current_job_receipt_handle: Optional[str] = None  # Graceful shutdown: 현재 처리 중인 작업 추적
_current_job_start_time: Optional[float] = None  # 로그 가시성: 작업 시작 시간

# Drain timeout: ffmpeg가 SIGTERM 무시 시 kill (Spot/scale-in 시)
DRAIN_TERMINATE_WAIT_SECONDS = 90

# SQS Long Polling 설정
SQS_WAIT_TIME_SECONDS = 20  # 최대 대기 시간 (Long Polling)
# Long Job 표준: 3시간 영상 대비 90초마다 900초 visibility 연장
VISIBILITY_EXTEND_SECONDS = 900  # change_message_visibility 호출 시 연장값
VISIBILITY_EXTEND_INTERVAL_SECONDS = 90  # 인코딩 중 연장 주기

# 3시간 영상 대비: 락/진행률 TTL (3h + margin = 4h). TTL 만료 시 중복 실행/진행률 소실 방지
VIDEO_LOCK_TTL_SECONDS = int(os.getenv("VIDEO_LOCK_TTL_SECONDS", "14400"))   # 4h
VIDEO_PROGRESS_TTL_SECONDS = int(os.getenv("VIDEO_PROGRESS_TTL_SECONDS", "14400"))  # 4h

# NACK 시 메시지 재노출 대기 (락 TTL 만료 후 재처리 허용) — lock_fail, skip:lock
NACK_VISIBILITY_SECONDS = 60  # 60~120 범위
NACK_VISIBILITY_MAX = 120

# failed transient 시 retry backoff (일시적 실패 시 즉시 재시도 방지)
FAILED_TRANSIENT_BACKOFF_SECONDS = 180  # 180~600 범위

# Job 기반: heartbeat + visibility 연장 주기 (엔터프라이즈 표준)
JOB_HEARTBEAT_INTERVAL_SECONDS = 60

# Job 최대 재시도 횟수 (초과 시 DEAD)
VIDEO_JOB_MAX_ATTEMPTS = int(os.environ.get("VIDEO_JOB_MAX_ATTEMPTS", "5"))


def _visibility_extender_loop(
    queue: "VideoSQSAdapter",
    receipt_handle: str,
    stop_event: threading.Event,
) -> None:
    """Long Job: ffmpeg 인코딩 동안 90초마다 visibility를 900초로 연장."""
    while not stop_event.wait(timeout=VISIBILITY_EXTEND_INTERVAL_SECONDS):
        try:
            queue.change_message_visibility(receipt_handle, VISIBILITY_EXTEND_SECONDS)
            logger.debug("Visibility extended receipt_handle=...%s", receipt_handle[-12:] if receipt_handle else "")
        except Exception as e:
            logger.warning("Visibility extend failed: %s", e)


def _heartbeat_loop(tenant_id: int, video_id: int, stop_event: threading.Event) -> None:
    """
    PROCESSING 동안 worker liveness 보장용 Redis heartbeat loop.
    progress 호출과 무관하게 20초 주기로 갱신.
    """
    while not stop_event.is_set():
        try:
            set_video_heartbeat(tenant_id, video_id, ttl_seconds=60)
        except Exception as e:
            logger.warning("Heartbeat set failed video_id=%s: %s", video_id, e)
        stop_event.wait(20)


def _job_visibility_and_heartbeat_loop(
    queue: "VideoSQSAdapter",
    receipt_handle: str,
    job_id: str,
    stop_event: threading.Event,
    cancel_event: threading.Event,
) -> None:
    """
    Job 기반: 60초마다 ChangeMessageVisibility + job_heartbeat 수행 후,
    DB에서 cancel_requested 확인. True이면 ffmpeg subprocess에 SIGTERM 전달.
    """
    from academy.adapters.db.django.repositories_video import job_heartbeat, job_is_cancel_requested
    from apps.worker.video_worker.current_transcode import get_current

    while not stop_event.wait(timeout=JOB_HEARTBEAT_INTERVAL_SECONDS):
        try:
            queue.change_message_visibility(receipt_handle, VISIBILITY_EXTEND_SECONDS)
            job_heartbeat(job_id, lease_seconds=VISIBILITY_EXTEND_SECONDS)
            process, proc_job_id, ev = get_current()
            if proc_job_id != job_id or process is None or process.poll() is not None:
                pass
            elif spot_termination_event.is_set():
                # Spot/scale-in drain: terminate, wait 90s, then kill (Drain timeout 보호)
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=DRAIN_TERMINATE_WAIT_SECONDS)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    cancel_event.set()
                    logger.info("DRAIN_INTERRUPT | job_id=%s | ffmpeg SIGTERM sent (wait=%ds)", job_id, DRAIN_TERMINATE_WAIT_SECONDS)
                except Exception as ex:
                    logger.warning("ffmpeg drain terminate/wait failed job_id=%s: %s", job_id, ex)
                    cancel_event.set()
            elif job_is_cancel_requested(job_id):
                # User retry cancel: 15s wait then kill
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    cancel_event.set()
                    logger.info("CANCEL_REQUESTED | job_id=%s | ffmpeg SIGTERM sent and waited", job_id)
                except Exception as ex:
                    logger.warning("ffmpeg terminate/wait failed job_id=%s: %s", job_id, ex)
                    cancel_event.set()
        except Exception as e:
            logger.warning("Job heartbeat/visibility failed job_id=%s: %s", job_id, e)


def _spot_interruption_poller(stop_event: threading.Event) -> None:
    """EC2 Spot terminate notice 감지. 5초마다 metadata poll, 200 시 spot_termination_event + Redis interrupt."""
    import urllib.request
    import urllib.error
    SPOT_METADATA_URL = "http://169.254.169.254/latest/meta-data/spot/instance-action"
    while not stop_event.wait(timeout=5):
        try:
            req = urllib.request.Request(SPOT_METADATA_URL, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    logger.warning("SPOT_INTERRUPTION_DETECTED | metadata returned 200")
                    spot_termination_event.set()
                    try:
                        from apps.support.video.redis_status_cache import set_asg_interrupt
                        set_asg_interrupt()
                    except Exception as e:
                        logger.warning("set_asg_interrupt failed: %s", e)
                    break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                pass  # no action scheduled
            else:
                logger.debug("Spot metadata poll HTTP error: %s", e.code)
        except Exception as e:
            logger.debug("Spot metadata poll failed: %s", e)
    logger.debug("Spot interruption poller stopped")


def _handle_signal(sig, frame):
    """
    Graceful shutdown (drain) 핸들러. SIGTERM 시 Spot/scale-in과 동일 drain (job_fail_retry + visibility=0).
    """
    global _shutdown, _current_job_receipt_handle
    try:
        signal_name = signal.Signals(sig).name
    except ValueError:
        signal_name = str(sig)
    logger.info(
        "Received %s, drain started — will finish current job and exit | current_job=%s",
        signal_name,
        "processing" if _current_job_receipt_handle else "idle",
    )
    _shutdown = True
    spot_termination_event.set()
    try:
        from apps.support.video.redis_status_cache import set_asg_interrupt
        set_asg_interrupt()
    except Exception as e:
        logger.warning("set_asg_interrupt failed: %s", e)


def main() -> None:
    """
    SQS 기반 Video Worker — strict single-job: 1건 처리 후 반드시 종료 (Ephemeral Worker).
    receive → process → delete → sys.exit(0). Daemon loop 없음.
    desired = Visible/1 → 인스턴스 1대당 1 job 처리 후 scale-in 대상.
    """
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    cfg = load_config()
    queue = VideoSQSAdapter()
    idempotency = RedisIdempotencyAdapter(ttl_seconds=VIDEO_LOCK_TTL_SECONDS)
    progress = RedisProgressAdapter(ttl_seconds=VIDEO_PROGRESS_TTL_SECONDS)

    logger.info(
        "Video Worker (SQS) single-job | queue=%s | wait_time=%ss",
        queue._get_queue_name(),
        SQS_WAIT_TIME_SECONDS,
    )

    stop_spot_poller = threading.Event()
    spot_poller_thread = threading.Thread(target=_spot_interruption_poller, args=(stop_spot_poller,), daemon=True)
    spot_poller_thread.start()

    receipt_handle = None
    try:
        wait_sec = 0 if (_shutdown or spot_termination_event.is_set()) else SQS_WAIT_TIME_SECONDS
        try:
            message = queue.receive_message(wait_time_seconds=wait_sec)
        except QueueUnavailableError as e:
            logger.warning("SQS unavailable. %s", e)
            sys.exit(1)

        if not message:
            logger.info("No message — exiting (scale-in target)")
            sys.exit(0)

        receipt_handle = message.get("receipt_handle")
        if not receipt_handle:
            logger.error("Message missing receipt_handle: %s", message)
            sys.exit(0)

        if _shutdown or spot_termination_event.is_set():
            try:
                queue.change_message_visibility(receipt_handle, 0)
            except Exception:
                pass
            logger.info("shutdown: returning message visibility=0")
            sys.exit(0)

        # ----- R2 삭제 작업 (비동기 삭제) -----
        if message.get("action") == "delete_r2":
            video_id = message.get("video_id")
            file_key = (message.get("file_key") or "").strip()
            hls_prefix = (message.get("hls_prefix") or "").strip()
            DELETE_R2_VISIBILITY = 900
            queue.change_message_visibility(receipt_handle, DELETE_R2_VISIBILITY)
            if not idempotency.acquire_lock(f"delete_r2:{video_id}"):
                logger.info("R2 delete skip (lock) video_id=%s", video_id)
                queue.delete_message(receipt_handle)
                sys.exit(0)
            try:
                from apps.infrastructure.storage.r2 import delete_object_r2_video, delete_prefix_r2_video
                if file_key:
                    delete_object_r2_video(key=file_key)
                    logger.info("R2 raw deleted video_id=%s key=%s", video_id, file_key)
                if hls_prefix:
                    def _extend_visibility(_):
                        queue.change_message_visibility(receipt_handle, DELETE_R2_VISIBILITY)
                    n = delete_prefix_r2_video(
                        prefix=hls_prefix,
                        on_batch_deleted=_extend_visibility,
                    )
                    logger.info("R2 HLS prefix deleted video_id=%s prefix=%s count=%d", video_id, hls_prefix, n)
            except Exception as e:
                logger.exception("R2 delete job failed video_id=%s: %s", video_id, e)
            finally:
                idempotency.release_lock(f"delete_r2:{video_id}")
            queue.delete_message(receipt_handle)
            sys.exit(0)

        # ----- 인코딩 작업 (Job 기반) -----
        job_id = message.get("job_id")
        video_id = message.get("video_id")
        file_key = message.get("file_key")
        tenant_id = message.get("tenant_id")
        tenant_code = message.get("tenant_code")
        message_created_at = message.get("created_at")

        if not video_id or tenant_id is None:
            logger.error("Invalid message format (video_id, tenant_id required): %s", message)
            queue.delete_message(receipt_handle)
            sys.exit(0)

        if not job_id:
            logger.warning("MESSAGE_LEGACY_SKIP | job_id missing | video_id=%s | NACK", video_id)
            queue.change_message_visibility(receipt_handle, NACK_VISIBILITY_SECONDS)
            sys.exit(0)

        from academy.adapters.db.django.repositories_video import (
            job_get_by_id,
            job_claim_for_running,
            job_complete,
            job_fail_retry,
            job_cancel,
            job_mark_dead,
            get_video_status,
        )
        from src.application.video.handler import CancelledError
        job_obj = job_get_by_id(job_id)
        if not job_obj:
            queue.delete_message(receipt_handle)
            logger.info("JOB_NOT_FOUND_DELETE | job_id=%s | video_id=%s | message consumed (video/job deleted)", job_id, video_id)
            sys.exit(0)

        if get_video_status(video_id) == "READY":
            queue.delete_message(receipt_handle)
            logger.info("VIDEO_ALREADY_READY_SKIP | job_id=%s | video_id=%s", job_id, video_id)
            sys.exit(0)

        request_id = str(uuid.uuid4())[:8]
        worker_id = f"{cfg.WORKER_ID}-{request_id}"
                message_received_at = time.time()
                try:
                    if message_created_at is None:
                        created_ts = message_received_at
                    elif isinstance(message_created_at, (int, float)):
                        created_ts = float(message_created_at)
                    else:
                        from datetime import datetime
                        dt = datetime.fromisoformat(str(message_created_at).replace("Z", "+00:00"))
                        created_ts = dt.timestamp()
                    queue_wait_time = message_received_at - created_ts
                except (ValueError, TypeError, AttributeError):
                    queue_wait_time = 0.0

                logger.info(
                    "SQS_MESSAGE_RECEIVED | job_id=%s | video_id=%s | tenant_id=%s | queue_wait_sec=%.2f",
                    job_id, video_id, tenant_id, queue_wait_time,
                )

                if not job_claim_for_running(job_id, worker_id, lease_seconds=3600):
                    logger.info("JOB_CLAIM_FAILED | job_id=%s | video_id=%s | NACK", job_id, video_id)
                    queue.change_message_visibility(receipt_handle, NACK_VISIBILITY_SECONDS)
                    return 0

                # Progress API가 PROCESSING 반환하도록 Redis에 캐시
                try:
                    from apps.support.video.redis_status_cache import cache_video_status
                    cache_video_status(tenant_id, video_id, "PROCESSING", ttl=21600)
                except Exception as ex:
                    logger.debug("cache PROCESSING failed: %s", ex)

                def _cancel_check():
                    from academy.adapters.db.django.repositories_video import job_is_cancel_requested
                    return job_is_cancel_requested(job_id)

                cancel_event = threading.Event()
                job_dict = {
                    "video_id": int(video_id),
                    "file_key": str(file_key or ""),
                    "tenant_id": int(tenant_id),
                    "tenant_code": str(tenant_code or ""),
                    "_cancel_check": _cancel_check,
                    "_job_id": job_id,
                    "_cancel_event": cancel_event,
                }

                if _cancel_check():
                    job_cancel(job_id)
                    queue.delete_message(receipt_handle)
                    logger.info("JOB_CANCELLED_SKIP | job_id=%s | video_id=%s", job_id, video_id)
                    return 0

                global _current_job_receipt_handle, _current_job_start_time
                _current_job_receipt_handle = receipt_handle
                _current_job_start_time = time.time()

                stop_heartbeat = threading.Event()
                heartbeat_thread = threading.Thread(
                    target=_job_visibility_and_heartbeat_loop,
                    args=(queue, receipt_handle, job_id, stop_heartbeat, cancel_event),
                    daemon=True,
                )
                heartbeat_thread.start()

                try:
                    # Idempotent 순서: 1) 스토리지(HLS) 2) DB 커밋 3) raw 삭제 4) DeleteMessage
                    # 중복 실행 시 process_video는 동일 경로 덮어쓰기, job_complete는 idempotent 반환
                    logger.info("[SQS_MAIN] process_video job_id=%s video_id=%s", job_id, video_id)
                    hls_path, duration = process_video(job=job_dict, cfg=cfg, progress=progress)
                    ok, reason = job_complete(job_id, hls_path, duration)
                    if not ok:
                        raise RuntimeError(f"job_complete failed: {reason}")

                    processing_duration = time.time() - _current_job_start_time

                    # R2 raw 삭제 (DB 커밋 이후 — 실패해도 DB 상태는 이미 READY)
                    file_key_for_raw = job_dict.get("file_key") or ""
                    if file_key_for_raw.strip():
                        from apps.infrastructure.storage.r2 import delete_object_r2_video
                        for attempt in range(3):
                            try:
                                delete_object_r2_video(key=file_key_for_raw.strip())
                                logger.info("R2 raw deleted after encode video_id=%s key=%s", video_id, file_key_for_raw[:80])
                                break
                            except Exception as e:
                                logger.warning("R2 raw delete failed video_id=%s attempt=%s: %s", video_id, attempt + 1, e)
                                if attempt < 2:
                                    time.sleep(2**attempt)

                    queue.delete_message(receipt_handle)
                    logger.info(
                        "SQS_JOB_COMPLETED | job_id=%s | video_id=%s | tenant_id=%s | processing_duration=%.2f | queue_wait_sec=%.2f",
                        job_id, video_id, tenant_id, processing_duration, queue_wait_time,
                    )
                    logger.info("VIDEO_ENCODING_DURATION | job_id=%s | video_id=%s | duration_sec=%.2f", job_id, video_id, processing_duration)
                    consecutive_errors = 0
                    logger.info("Single-job complete — exiting (scale-in target)")
                    return 0

                except CancelledError:
                    if spot_termination_event.is_set():
                        job_fail_retry(job_id, "DRAIN_INTERRUPT")
                        queue.change_message_visibility(receipt_handle, 0)
                        logger.info("SPOT_DRAIN_COMPLETED | job_id=%s | video_id=%s | visibility=0 (no delete)", job_id, video_id)
                    else:
                        job_cancel(job_id)
                        queue.delete_message(receipt_handle)
                        logger.info("JOB_CANCELLED | job_id=%s | video_id=%s", job_id, video_id)
                    consecutive_errors = 0

                except Exception as e:
                    logger.exception("JOB_PROCESSING_FAILED | job_id=%s | video_id=%s | error=%s", job_id, video_id, e)
                    job_fail_retry(job_id, str(e)[:2000])
                    job_after = job_get_by_id(job_id)
                    if job_after and job_after.attempt_count >= VIDEO_JOB_MAX_ATTEMPTS:
                        job_mark_dead(job_id, error_code="MAX_ATTEMPTS", error_message=str(e)[:2000])
                        logger.warning("JOB_DEAD | job_id=%s | video_id=%s | attempt_count=%s", job_id, video_id, job_after.attempt_count)
                    queue.change_message_visibility(receipt_handle, FAILED_TRANSIENT_BACKOFF_SECONDS)
                    consecutive_errors += 1
                    return 1

                finally:
                    stop_heartbeat.set()
                    heartbeat_thread.join(timeout=3)
                    try:
                        delete_video_heartbeat(tenant_id, video_id)
                    except Exception:
                        pass

                _current_job_receipt_handle = None
                _current_job_start_time = None
                return 0

            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                return 0
            except Exception as e:
                try:
                    if receipt_handle:
                        queue.change_message_visibility(receipt_handle, 0)
                except Exception:
                    pass
                logger.exception("Unexpected error in main loop: %s", e)
                return 1

        return 0
        
    except Exception:
        logger.exception("Fatal error in Video Worker")
        return 1


if __name__ == "__main__":
    sys.exit(main())
