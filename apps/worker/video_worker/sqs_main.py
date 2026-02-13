"""
Video Worker - SQS 기반 메인 엔트리포인트

기존 HTTP polling 방식에서 SQS Long Polling으로 전환
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import uuid
from typing import Optional

import boto3
import requests

from apps.worker.video_worker.config import load_config
from src.infrastructure.video import VideoSQSAdapter
from src.infrastructure.video.processor import process_video
from src.infrastructure.db.video_repository import VideoRepository
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
SQS_VISIBILITY_TIMEOUT = 300  # 메시지 처리 시간 (5분)

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
        
        logger.info("EC2 instance stopped due to idle queues: instance_id=%s", instance_id)
        
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
    repo = VideoRepository()
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
                message = queue.receive_message(wait_time_seconds=SQS_WAIT_TIME_SECONDS)
                
                if not message:
                    consecutive_empty_polls += 1
                    consecutive_errors = 0
                    
                    # 연속 빈 폴링이 임계값을 초과하면 EC2 인스턴스 종료
                    if consecutive_empty_polls >= IDLE_STOP_THRESHOLD:
                        logger.info(
                            "Queue empty for %d consecutive polls (threshold=%d), stopping EC2 instance",
                            consecutive_empty_polls,
                            IDLE_STOP_THRESHOLD,
                        )
                        _stop_self_ec2()
                        return 0
                    
                    continue
                
                # 메시지가 있으면 카운터 리셋
                consecutive_empty_polls = 0
                
                receipt_handle = message.get("receipt_handle")
                if not receipt_handle:
                    logger.error("Message missing receipt_handle: %s", message)
                    continue
                
                # 메시지에서 작업 데이터 추출
                video_id = message.get("video_id")
                file_key = message.get("file_key")
                tenant_code = message.get("tenant_code")
                message_created_at = message.get("created_at")  # SQS 메시지 수명 추적
                
                if not video_id or not tenant_code:
                    logger.error("Invalid message format: %s", message)
                    queue.delete_message(receipt_handle)
                    continue

                request_id = str(uuid.uuid4())[:8]
                message_received_at = time.time()
                queue_wait_time = message_received_at - (float(message_created_at) if message_created_at else message_received_at)

                logger.info(
                    "SQS_MESSAGE_RECEIVED | request_id=%s | video_id=%s | tenant_code=%s | queue_wait_sec=%.2f | created_at=%s",
                    request_id,
                    video_id,
                    tenant_code,
                    queue_wait_time,
                    message_created_at or "unknown",
                )

                global _current_job_receipt_handle, _current_job_start_time
                _current_job_receipt_handle = receipt_handle
                _current_job_start_time = time.time()

                job = {
                    "video_id": int(video_id),
                    "file_key": str(file_key or ""),
                    "tenant_code": str(tenant_code),
                }

                result = handler.handle(job, cfg)

                processing_duration = time.time() - _current_job_start_time
                _current_job_receipt_handle = None
                _current_job_start_time = None

                if result == "ok":
                    queue.delete_message(receipt_handle)
                    logger.info(
                        "SQS_JOB_COMPLETED | request_id=%s | video_id=%s | tenant_code=%s | processing_duration=%.2f | queue_wait_sec=%.2f",
                        request_id,
                        video_id,
                        tenant_code,
                        processing_duration,
                        queue_wait_time,
                    )
                    consecutive_errors = 0

                    if _shutdown:
                        logger.info("Graceful shutdown: current job completed, exiting")
                        break

                elif result == "skip":
                    queue.delete_message(receipt_handle)
                    consecutive_errors = 0

                else:
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
                logger.exception("Unexpected error in main loop: %s", e)
                consecutive_errors += 1
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        "Too many consecutive errors (%s), shutting down",
                        consecutive_errors,
                    )
                    return 1
                
                # 에러 후 짧은 대기
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
