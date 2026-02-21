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
import threading
import time
import uuid
from typing import Optional

from apps.worker.video_worker.config import load_config
from libs.queue import QueueUnavailableError
from src.infrastructure.video import VideoSQSAdapter
from src.infrastructure.video.processor import process_video
from academy.adapters.db.django.repositories_video import DjangoVideoRepository
from src.infrastructure.cache.redis_idempotency_adapter import RedisIdempotencyAdapter
from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter
from src.application.video.handler import ProcessVideoJobHandler
from apps.support.video.redis_status_cache import set_video_heartbeat, delete_video_heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [VIDEO-WORKER-SQS] %(message)s",
)
logger = logging.getLogger("video_worker_sqs")

_shutdown = False
_current_job_receipt_handle: Optional[str] = None  # Graceful shutdown: 현재 처리 중인 작업 추적
_current_job_start_time: Optional[float] = None  # 로그 가시성: 작업 시작 시간

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

# 빠른 ACK 모드: receive 직후 delete → inflight 스케일 왜곡 제거. DB try_claim으로 중복 방지.
VIDEO_FAST_ACK = os.environ.get("VIDEO_FAST_ACK", "0") == "1"


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


def _handle_signal(sig, frame):
    """
    Graceful shutdown (drain) 핸들러.
    SIGTERM 수신 시 SQS poll 중단 요청, 진행 중 job 있으면 완료 후 종료.
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


def main() -> int:
    """
    SQS 기반 Video Worker 메인 루프
    
    Flow:
    1. SQS에서 메시지 Long Polling
    2. 메시지 수신 시 비디오 처리
    3. 성공 시 메시지 삭제
    4. 실패 시 메시지는 SQS가 자동으로 재시도 (DLQ로 전송 전까지)
    """
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    
    cfg = load_config()
    queue = VideoSQSAdapter()
    repo = DjangoVideoRepository()
    idempotency = RedisIdempotencyAdapter(ttl_seconds=VIDEO_LOCK_TTL_SECONDS)
    progress = RedisProgressAdapter(ttl_seconds=VIDEO_PROGRESS_TTL_SECONDS)
    handler = ProcessVideoJobHandler(
        repo=repo,
        idempotency=idempotency,
        progress=progress,
        process_fn=process_video,
    )

    logger.info(
        "Video Worker (SQS) started | queue=%s | wait_time=%ss",
        queue._get_queue_name(),
        SQS_WAIT_TIME_SECONDS,
    )
    
    consecutive_errors = 0
    max_consecutive_errors = 10

    try:
        while not _shutdown:
            try:
                # SQS Long Polling으로 메시지 수신
                try:
                    message = queue.receive_message(wait_time_seconds=SQS_WAIT_TIME_SECONDS)
                except QueueUnavailableError as e:
                    # 로컬 등 AWS 자격 증명 없을 때: 로그 한 번, 60초 대기 후 재시도
                    logger.warning(
                        "SQS unavailable (AWS credentials invalid or missing?). Waiting 60s before retry. %s",
                        e,
                    )
                    time.sleep(60)
                    continue

                if not message:
                    consecutive_errors = 0
                    continue

                # SIGTERM 수신 시 새 메시지는 즉시 visibility=0 반환 후 종료
                if _shutdown:
                    receipt_handle = message.get("receipt_handle")
                    if receipt_handle:
                        try:
                            queue.change_message_visibility(receipt_handle, 0)
                        except Exception:
                            pass
                        logger.info("shutdown: returning message visibility=0")
                    break

                receipt_handle = message.get("receipt_handle")
                if not receipt_handle:
                    logger.error("Message missing receipt_handle: %s", message)
                    continue

                # ----- R2 삭제 작업 (비동기 삭제) -----
                if message.get("action") == "delete_r2":
                    video_id = message.get("video_id")
                    file_key = (message.get("file_key") or "").strip()
                    hls_prefix = (message.get("hls_prefix") or "").strip()
                    # delete_r2 전용 visibility 900초. 장시간 삭제 시 배치마다 재연장.
                    DELETE_R2_VISIBILITY = 900
                    queue.change_message_visibility(receipt_handle, DELETE_R2_VISIBILITY)
                    # action별 멱등: 삭제 중복 처리 방지
                    if not idempotency.acquire_lock(f"delete_r2:{video_id}"):
                        logger.info("R2 delete skip (lock) video_id=%s", video_id)
                        queue.delete_message(receipt_handle)
                        continue
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
                    continue

                # ----- 인코딩 작업 -----
                video_id = message.get("video_id")
                file_key = message.get("file_key")
                tenant_id = message.get("tenant_id")
                tenant_code = message.get("tenant_code")
                message_created_at = message.get("created_at")

                if not video_id or tenant_id is None:
                    logger.error("Invalid message format (video_id, tenant_id required): %s", message)
                    queue.delete_message(receipt_handle)
                    continue

                # Retry로 이미 완료된 영상이면 visibility 연장 없이 메시지만 삭제 (중복·3시간 묶임 방지)
                from academy.adapters.db.django.repositories_video import get_video_status
                if get_video_status(video_id) == "READY":
                    queue.delete_message(receipt_handle)
                    logger.info("VIDEO_ALREADY_READY_SKIP | video_id=%s (retry 등으로 이미 완료)", video_id)
                    continue

                request_id = str(uuid.uuid4())[:8]
                message_received_at = time.time()
                # created_at: Unix float 또는 ISO 8601 문자열 (timezone.now().isoformat())
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
                    "SQS_MESSAGE_RECEIVED | request_id=%s | video_id=%s | tenant_id=%s | queue_wait_sec=%.2f | created_at=%s | fast_ack=%s",
                    request_id,
                    video_id,
                    tenant_id,
                    queue_wait_time,
                    message_created_at or "unknown",
                    VIDEO_FAST_ACK,
                )

                if VIDEO_FAST_ACK:
                    queue.delete_message(receipt_handle)
                    receipt_suffix = receipt_handle[-12:] if receipt_handle and len(receipt_handle) >= 12 else (receipt_handle or "")[:12]
                    logger.info(
                        "VIDEO_FAST_ACK_APPLIED | request_id=%s | video_id=%s | receipt_handle_suffix=%s",
                        request_id,
                        video_id,
                        receipt_suffix,
                    )
                else:
                    logger.info(
                        "VIDEO_FAST_ACK_SKIPPED | request_id=%s | video_id=%s | reason=VIDEO_FAST_ACK=0",
                        request_id,
                        video_id,
                    )

                global _current_job_receipt_handle, _current_job_start_time
                _current_job_receipt_handle = receipt_handle
                _current_job_start_time = time.time()

                job = {
                    "video_id": int(video_id),
                    "file_key": str(file_key or ""),
                    "tenant_id": int(tenant_id),
                    "tenant_code": str(tenant_code or ""),
                }
                if VIDEO_FAST_ACK:
                    job["_worker_id"] = f"{cfg.WORKER_ID}-{request_id}"

                if VIDEO_FAST_ACK:
                    heartbeat_stop = threading.Event()
                    heartbeat_thread = threading.Thread(
                        target=_heartbeat_loop,
                        args=(tenant_id, video_id, heartbeat_stop),
                        daemon=True,
                    )
                    heartbeat_thread.start()
                    try:
                        logger.info("[SQS_MAIN] Calling handler.handle() video_id=%s (fast_ack)", video_id)
                        result = handler.handle(job, cfg)
                        logger.info("[SQS_MAIN] handler.handle() returned video_id=%s result=%s", video_id, result)
                    except Exception as e:
                        logger.exception("[SQS_MAIN] Handler exception (fast_ack, message already deleted): video_id=%s: %s", video_id, e)
                        consecutive_errors += 1
                        _current_job_receipt_handle = None
                        _current_job_start_time = None
                        if consecutive_errors >= max_consecutive_errors:
                            logger.error("Too many consecutive errors (%s), shutting down", consecutive_errors)
                            return 1
                        time.sleep(5)
                        continue
                    finally:
                        heartbeat_stop.set()
                        heartbeat_thread.join(timeout=2)
                        try:
                            delete_video_heartbeat(tenant_id, video_id)
                        except Exception:
                            pass
                else:
                    stop_extender = threading.Event()
                    extender = threading.Thread(
                        target=_visibility_extender_loop,
                        args=(queue, receipt_handle, stop_extender),
                        daemon=True,
                    )
                    extender.start()
                    heartbeat_stop = threading.Event()
                    heartbeat_thread = threading.Thread(
                        target=_heartbeat_loop,
                        args=(tenant_id, video_id, heartbeat_stop),
                        daemon=True,
                    )
                    heartbeat_thread.start()
                    try:
                        logger.info("[SQS_MAIN] Calling handler.handle() video_id=%s", video_id)
                        result = handler.handle(job, cfg)
                        logger.info("[SQS_MAIN] handler.handle() returned video_id=%s result=%s", video_id, result)
                    except Exception as e:
                        stop_extender.set()
                        extender.join(timeout=1)
                        queue.change_message_visibility(receipt_handle, 0)
                        logger.exception("[SQS_MAIN] Handler exception (visibility 0 applied): video_id=%s: %s", video_id, e)
                        consecutive_errors += 1
                        _current_job_receipt_handle = None
                        _current_job_start_time = None
                        if consecutive_errors >= max_consecutive_errors:
                            logger.error("Too many consecutive errors (%s), shutting down", consecutive_errors)
                            return 1
                        time.sleep(5)
                        continue
                    finally:
                        heartbeat_stop.set()
                        heartbeat_thread.join(timeout=2)
                        try:
                            delete_video_heartbeat(tenant_id, video_id)
                        except Exception:
                            pass
                        stop_extender.set()
                        extender.join(timeout=1)

                processing_duration = time.time() - _current_job_start_time
                _current_job_receipt_handle = None
                _current_job_start_time = None

                if result == "ok":
                    # 인코딩 성공 → HLS 업로드·DB 완료 후 raw 삭제 (실패해도 DB 롤백 안 함, 재시도만)
                    file_key_for_raw = job.get("file_key") or ""
                    if file_key_for_raw.strip():
                        from apps.infrastructure.storage.r2 import delete_object_r2_video
                        for attempt in range(3):
                            try:
                                delete_object_r2_video(key=file_key_for_raw.strip())
                                logger.info("R2 raw deleted after encode video_id=%s key=%s", video_id, file_key_for_raw[:80])
                                break
                            except Exception as e:
                                logger.warning(
                                    "R2 raw delete after encode failed video_id=%s attempt=%s: %s",
                                    video_id, attempt + 1, e,
                                )
                                if attempt < 2:
                                    time.sleep(2**attempt)  # 1s → 2s → 4s exponential backoff
                    if not VIDEO_FAST_ACK:
                        queue.delete_message(receipt_handle)
                    logger.info(
                        "SQS_JOB_COMPLETED | request_id=%s | video_id=%s | tenant_code=%s | processing_duration=%.2f | queue_wait_sec=%.2f",
                        request_id,
                        video_id,
                        tenant_code,
                        processing_duration,
                        queue_wait_time,
                    )
                    # 평균 인코딩 시간 모니터링용 (로그 파싱·메트릭 수집)
                    logger.info(
                        "VIDEO_ENCODING_DURATION | video_id=%s | duration_sec=%.2f",
                        video_id,
                        processing_duration,
                    )
                    consecutive_errors = 0

                    if _shutdown:
                        logger.info("drain complete — current job finished, exiting")
                        break

                elif result == "skip:cancel":
                    logger.info(
                        "cancel requested — ack/delete | request_id=%s | video_id=%s",
                        request_id,
                        video_id,
                    )
                    if not VIDEO_FAST_ACK:
                        queue.delete_message(receipt_handle)
                    consecutive_errors = 0

                elif result == "skip:claim":
                    logger.info(
                        "skip:claim — already acked | request_id=%s | video_id=%s",
                        request_id,
                        video_id,
                    )
                    consecutive_errors = 0

                elif result == "skip:lock":
                    logger.info(
                        "skip:lock — nack | request_id=%s | video_id=%s",
                        request_id,
                        video_id,
                    )
                    if not VIDEO_FAST_ACK:
                        queue.change_message_visibility(receipt_handle, NACK_VISIBILITY_SECONDS)
                    consecutive_errors = 0

                elif result == "skip:mark_processing":
                    logger.info(
                        "skip:mark_processing — nack | request_id=%s | video_id=%s",
                        request_id,
                        video_id,
                    )
                    if not VIDEO_FAST_ACK:
                        queue.change_message_visibility(receipt_handle, NACK_VISIBILITY_SECONDS)
                    consecutive_errors = 0

                elif result == "lock_fail":
                    logger.info(
                        "lock_fail — nack | request_id=%s | video_id=%s",
                        request_id,
                        video_id,
                    )
                    if not VIDEO_FAST_ACK:
                        queue.change_message_visibility(receipt_handle, NACK_VISIBILITY_SECONDS)
                    consecutive_errors = 0

                elif result == "skip":
                    logger.warning(
                        "legacy skip — nack | request_id=%s | video_id=%s",
                        request_id,
                        video_id,
                    )
                    if not VIDEO_FAST_ACK:
                        queue.change_message_visibility(receipt_handle, NACK_VISIBILITY_SECONDS)
                    consecutive_errors = 0

                else:
                    # failed — legacy: NACK. fast_ack: 메시지 이미 삭제됨, DB FAILED 상태. 재시도는 별도 enqueue.
                    if not VIDEO_FAST_ACK:
                        queue.change_message_visibility(receipt_handle, FAILED_TRANSIENT_BACKOFF_SECONDS)
                    logger.warning(
                        "processing failed (transient) — nack backoff (%ss) | video_id=%s",
                        FAILED_TRANSIENT_BACKOFF_SECONDS,
                        video_id,
                    )
                    logger.exception(
                        "SQS_JOB_FAILED | request_id=%s | video_id=%s | tenant_code=%s | processing_duration=%.2f | queue_wait_sec=%.2f",
                        request_id,
                        video_id,
                        tenant_code,
                        processing_duration,
                        queue_wait_time,
                    )
                    consecutive_errors += 1

                    if consecutive_errors >= max_consecutive_errors:
                        logger.error(
                            "Too many consecutive errors (%s), shutting down",
                            consecutive_errors,
                        )
                        return 1
                
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                break
            except Exception as e:
                # 예외 발생 시에도 visibility 0 시도 (이미 delete된 메시지면 API 오류는 무시)
                try:
                    queue.change_message_visibility(receipt_handle, 0)
                except Exception:
                    pass
                logger.exception("Unexpected error in main loop: %s", e)
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        "Too many consecutive errors (%s), shutting down",
                        consecutive_errors,
                    )
                    return 1
                time.sleep(5)
        
        # Drain: break 시점에 current job은 이미 완료됨 (완료 후에만 break)
        if _current_job_receipt_handle:
            logger.info(
                "drain: waiting for current job to complete | receipt_handle=%s",
                _current_job_receipt_handle[:20] + "...",
            )
        logger.info("Video Worker shutdown complete | drain complete — safe to terminate")
        return 0
        
    except Exception:
        logger.exception("Fatal error in Video Worker")
        return 1


if __name__ == "__main__":
    sys.exit(main())
