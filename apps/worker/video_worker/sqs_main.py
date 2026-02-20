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
# 동적 visibility: 큐 기본 300초, ffmpeg 인코딩 동안 240초마다 300초 연장
VISIBILITY_EXTEND_SECONDS = 300  # change_message_visibility 호출 시 연장값 (큐 기본과 동일)
VISIBILITY_EXTEND_INTERVAL_SECONDS = 240  # 인코딩 중 연장 주기

# 3시간 영상 대비: 락/진행률 TTL (3h + margin = 4h). TTL 만료 시 중복 실행/진행률 소실 방지
VIDEO_LOCK_TTL_SECONDS = int(os.getenv("VIDEO_LOCK_TTL_SECONDS", "14400"))   # 4h
VIDEO_PROGRESS_TTL_SECONDS = int(os.getenv("VIDEO_PROGRESS_TTL_SECONDS", "14400"))  # 4h

# NACK 시 메시지 재노출 대기 (락 TTL 만료 후 재처리 허용)
NACK_VISIBILITY_SECONDS = 60

# failed 시 retry backoff (일시적 실패 시 즉시 재시도 방지)
FAILED_BACKOFF_SECONDS = 180


def _visibility_extender_loop(
    queue: "VideoSQSAdapter",
    receipt_handle: str,
    stop_event: threading.Event,
) -> None:
    """ffmpeg 인코딩 동안 240초마다 visibility를 300초로 연장."""
    while not stop_event.wait(timeout=VISIBILITY_EXTEND_INTERVAL_SECONDS):
        try:
            queue.change_message_visibility(receipt_handle, VISIBILITY_EXTEND_SECONDS)
            logger.debug("Visibility extended receipt_handle=...%s", receipt_handle[-12:] if receipt_handle else "")
        except Exception as e:
            logger.warning("Visibility extend failed: %s", e)


def _handle_signal(sig, frame):
    """
    Graceful shutdown 핸들러
    
    50명 원장 확장 대비: 현재 처리 중인 작업 완료 후 종료
    """
    global _shutdown, _current_job_receipt_handle
    signal_name = signal.Signals(sig).name
    logger.info(
        "Received %s, initiating graceful shutdown... | current_job=%s",
        signal_name,
        "processing" if _current_job_receipt_handle else "idle",
    )
    _shutdown = True
    # 현재 작업이 있으면 완료될 때까지 대기 (메인 루프에서 처리)


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
                    "SQS_MESSAGE_RECEIVED | request_id=%s | video_id=%s | tenant_id=%s | queue_wait_sec=%.2f | created_at=%s",
                    request_id,
                    video_id,
                    tenant_id,
                    queue_wait_time,
                    message_created_at or "unknown",
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

                stop_extender = threading.Event()
                extender = threading.Thread(
                    target=_visibility_extender_loop,
                    args=(queue, receipt_handle, stop_extender),
                    daemon=True,
                )
                extender.start()
                try:
                    logger.info("[SQS_MAIN] Calling handler.handle() video_id=%s", video_id)
                    result = handler.handle(job, cfg)
                    logger.info("[SQS_MAIN] handler.handle() returned video_id=%s result=%s", video_id, result)
                except Exception as e:
                    # handler.handle() 예외 시에도 반드시 즉시 재노출
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
                        logger.info("Graceful shutdown: current job completed, exiting")
                        break

                elif result == "skip:cancel":
                    # 취소 요청 또는 처리 중 취소 → ACK(delete)
                    logger.info(
                        "cancel requested — ack/delete | request_id=%s | video_id=%s",
                        request_id,
                        video_id,
                    )
                    queue.delete_message(receipt_handle)
                    consecutive_errors = 0

                elif result == "skip:lock":
                    # 락 실패(경합/잔류락) → NACK. delete 금지.
                    logger.info(
                        "lock contention or stale lock — returning message to queue | request_id=%s | video_id=%s",
                        request_id,
                        video_id,
                    )
                    queue.change_message_visibility(receipt_handle, NACK_VISIBILITY_SECONDS)
                    consecutive_errors = 0

                elif result == "skip:mark_processing":
                    # mark_processing 실패(이미 처리됨 등) → NACK. delete 금지.
                    logger.info(
                        "mark_processing failed — returning message to queue | request_id=%s | video_id=%s",
                        request_id,
                        video_id,
                    )
                    queue.change_message_visibility(receipt_handle, NACK_VISIBILITY_SECONDS)
                    consecutive_errors = 0

                elif result == "lock_fail":
                    # Redis 락 경합/이전 워커 크래시 → NACK. visibility 60초 후 재노출
                    # SQS가 재시도 책임, Redis lock TTL 만료 후 다른 워커가 처리 가능
                    logger.info(
                        "lock contention or stale lock — returning message to queue | request_id=%s | video_id=%s",
                        request_id,
                        video_id,
                    )
                    queue.change_message_visibility(receipt_handle, NACK_VISIBILITY_SECONDS)
                    consecutive_errors = 0

                elif result == "skip":
                    # 레거시/미지정 skip → NACK (stuck orphan 방지)
                    logger.warning(
                        "legacy skip — nack for safety | request_id=%s | video_id=%s",
                        request_id,
                        video_id,
                    )
                    queue.change_message_visibility(receipt_handle, NACK_VISIBILITY_SECONDS)
                    consecutive_errors = 0

                else:
                    # handler 실패(failed 등) → retry backoff 적용
                    queue.change_message_visibility(receipt_handle, FAILED_BACKOFF_SECONDS)
                    logger.warning(
                        "processing failed — applying retry backoff (%ss) | video_id=%s",
                        FAILED_BACKOFF_SECONDS,
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
        
        # Graceful shutdown: 현재 작업이 있으면 완료 대기
        if _current_job_receipt_handle:
            logger.info(
                "Graceful shutdown: waiting for current job to complete | receipt_handle=%s",
                _current_job_receipt_handle[:20] + "...",
            )
        
        logger.info("Video Worker shutdown complete")
        return 0
        
    except Exception:
        logger.exception("Fatal error in Video Worker")
        return 1


if __name__ == "__main__":
    sys.exit(main())
