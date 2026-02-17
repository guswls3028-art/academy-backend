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
import time
import uuid
from typing import Optional

import boto3
import requests

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
# 작업 시작 시 ChangeMessageVisibility로 연장 (3시간 영상 등 장시간 대비)
VIDEO_VISIBILITY_EXTEND_SECONDS = int(os.getenv("VIDEO_SQS_VISIBILITY_EXTEND", "10800"))  # 3시간
SQS_VISIBILITY_TIMEOUT = 300  # 로그 비교용 (실제는 VIDEO_VISIBILITY_EXTEND_SECONDS 사용)

# EC2 Self-Stop 설정 (비용 최적화)
IDLE_STOP_THRESHOLD = int(os.getenv("EC2_IDLE_STOP_THRESHOLD", "5"))  # 연속 빈 폴링 5회 = 100초


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


def _stop_self_ec2() -> None:
    """
    SQS 큐가 연속으로 비어있을 때 EC2 인스턴스 자동 종료
    
    비용 최적화: idle 상태 인스턴스 자동 종료로 월 $30-50 절감
    IMDSv2를 사용하여 안전하게 인스턴스 메타데이터 조회
    """
    try:
        # EC2 메타데이터에서 인스턴스 정보 가져오기 (IMDSv2)
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
        
        logger.info("EC2 instance stopped due to idle queues: instance_id=%s (video worker)", instance_id)
        
    except Exception as e:
        logger.exception("EC2 self-stop failed (ignored): %s", e)


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
    idempotency = RedisIdempotencyAdapter()
    progress = RedisProgressAdapter()
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
    consecutive_empty_polls = 0  # 비용 최적화: 빈 폴링 카운터
    
    try:
        while not _shutdown:
            try:
                # SQS Long Polling으로 메시지 수신
                try:
                    message = queue.receive_message(wait_time_seconds=SQS_WAIT_TIME_SECONDS)
                except QueueUnavailableError as e:
                    # 로컬 등 AWS 자격 증명 없을 때: 로그 한 번, 60초 대기 후 재시도 (empty로 세지 않음 → EC2 종료 안 함)
                    logger.warning(
                        "SQS unavailable (AWS credentials invalid or missing?). Waiting 60s before retry. %s",
                        e,
                    )
                    time.sleep(60)
                    continue

                if not message:
                    consecutive_empty_polls += 1
                    consecutive_errors = 0

                    # 연속 빈 폴링이 임계값을 초과하면 EC2 인스턴스 종료 (실제 큐가 비었을 때만)
                    if consecutive_empty_polls >= IDLE_STOP_THRESHOLD:
                        logger.info(
                            "Queue empty for %d consecutive polls (threshold=%d), stopping EC2 instance in 10s",
                            consecutive_empty_polls,
                            IDLE_STOP_THRESHOLD,
                        )
                        time.sleep(10)  # 500 plan: Dead zone 완화를 위해 Stop 직전 대기
                        logger.info("EC2 self-stop initiating (video worker)")
                        _stop_self_ec2()
                        return 0

                    continue

                # 메시지가 있으면 카운터 리셋
                consecutive_empty_polls = 0
                
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

                # 장시간 인코딩 시 재노출 방지 (작업 시작 직후 visibility 연장)
                queue.change_message_visibility(receipt_handle, VIDEO_VISIBILITY_EXTEND_SECONDS)

                request_id = str(uuid.uuid4())[:8]
                message_received_at = time.time()
                queue_wait_time = message_received_at - (float(message_created_at) if message_created_at else message_received_at)

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

                try:
                    result = handler.handle(job, cfg)
                except Exception as e:
                    # handler.handle() 예외 시에도 반드시 즉시 재노출 (3시간 묶임 방지)
                    queue.change_message_visibility(receipt_handle, 0)
                    logger.exception("Handler exception (visibility 0 applied): video_id=%s: %s", video_id, e)
                    consecutive_errors += 1
                    _current_job_receipt_handle = None
                    _current_job_start_time = None
                    if consecutive_errors >= max_consecutive_errors:
                        logger.error("Too many consecutive errors (%s), shutting down", consecutive_errors)
                        return 1
                    time.sleep(5)
                    continue

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

                elif result == "skip":
                    queue.delete_message(receipt_handle)
                    consecutive_errors = 0

                else:
                    # handler 실패 시 즉시 재노출 → 다른 워커가 곧바로 처리 (3시간 묶임 방지)
                    queue.change_message_visibility(receipt_handle, 0)
                    logger.exception(
                        "SQS_JOB_FAILED | request_id=%s | video_id=%s | tenant_code=%s | processing_duration=%.2f | queue_wait_sec=%.2f",
                        request_id,
                        video_id,
                        tenant_code,
                        processing_duration,
                        queue_wait_time,
                    )
                    if processing_duration > SQS_VISIBILITY_TIMEOUT:
                        logger.warning(
                            "SQS_VISIBILITY_TIMEOUT_EXCEEDED | request_id=%s | video_id=%s | processing_duration=%.2f | visibility_timeout=%d",
                            request_id,
                            video_id,
                            processing_duration,
                            SQS_VISIBILITY_TIMEOUT,
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
