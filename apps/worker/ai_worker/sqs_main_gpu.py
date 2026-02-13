"""
AI Worker GPU - SQS 기반 메인 엔트리포인트

Premium 큐 처리 (향후 GPU 지원)
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

from apps.worker.ai_worker.ai.pipelines.dispatcher import handle_ai_job
from apps.worker.ai_worker.ai.pipelines.tier_enforcer import enforce_tier_limits
from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from src.infrastructure.ai import AISQSAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AI-WORKER-GPU] %(message)s",
)
logger = logging.getLogger("ai_worker_gpu")

_shutdown = False

# SQS Long Polling 설정
SQS_WAIT_TIME_SECONDS = 20  # 최대 대기 시간 (Long Polling)
SQS_VISIBILITY_TIMEOUT = 600  # 메시지 처리 시간 (10분, GPU 작업은 더 오래 걸릴 수 있음)

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
    SQS 기반 AI Worker GPU 메인 루프
    
    Premium 큐 처리 (향후 GPU 지원)
    
    Flow:
    1. SQS Long Polling으로 Premium 큐에서 메시지 수신
    2. 메시지 수신 시 AI 작업 처리 (GPU 사용)
    3. 성공 시 메시지 삭제
    4. 실패 시 메시지는 SQS가 자동으로 재시도 (DLQ로 전송 전까지)
    """
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    
    queue = AISQSAdapter()
    
    logger.info(
        "AI Worker GPU started | queue=premium | wait_time=%ss",
        SQS_WAIT_TIME_SECONDS,
    )
    
    consecutive_errors = 0
    max_consecutive_errors = 10
    consecutive_empty_polls = 0  # 비용 최적화: 빈 폴링 카운터
    
    try:
        while not _shutdown:
            try:
                # SQS Long Polling으로 Premium 큐에서 메시지 수신
                message = queue.receive_message(
                    tier="premium",
                    wait_time_seconds=SQS_WAIT_TIME_SECONDS,
                )
                
                if not message:
                    consecutive_empty_polls += 1
                    consecutive_errors = 0
                    
                    # 연속 빈 폴링이 임계값을 초과하면 EC2 인스턴스 종료
                    if consecutive_empty_polls >= IDLE_STOP_THRESHOLD:
                        logger.info(
                            "All queues empty for %d consecutive polls (threshold=%d), stopping EC2 instance",
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
                job_id = message.get("job_id")
                job_type = message.get("job_type")
                tier = message.get("tier", "premium")
                payload = message.get("payload", {})
                message_created_at = message.get("created_at")  # SQS 메시지 수명 추적
                
                if not job_id or not job_type:
                    logger.error("Invalid message format: %s", message)
                    # 잘못된 메시지는 삭제하여 DLQ로 이동하지 않도록
                    queue.delete_message(receipt_handle, tier=tier)
                    continue
                
                # 로그 가시성: request_id 생성 및 메시지 수명 추적
                request_id = str(uuid.uuid4())[:8]
                message_received_at = time.time()
                queue_wait_time = message_received_at - (float(message_created_at) if message_created_at else message_received_at)
                
                logger.info(
                    "SQS_MESSAGE_RECEIVED | request_id=%s | job_id=%s | job_type=%s | tier=%s | queue_wait_sec=%.2f | created_at=%s",
                    request_id,
                    job_id,
                    job_type,
                    tier,
                    queue_wait_time,
                    message_created_at or "unknown",
                )
                
                # Graceful shutdown: 현재 작업 추적 시작
                global _current_job_receipt_handle, _current_job_start_time
                _current_job_receipt_handle = receipt_handle
                _current_job_start_time = time.time()
                
                # Tier 제한 검증 (Premium만 허용)
                if tier != "premium":
                    logger.error(
                        "GPU worker only processes premium tier: job_id=%s, tier=%s",
                        job_id,
                        tier,
                    )
                    queue.fail_job(
                        job_id=job_id,
                        error_message=f"GPU worker only processes premium tier, got {tier}",
                    )
                    queue.delete_message(receipt_handle, tier=tier)
                    continue
                
                # Tier 제한 검증
                allowed, error_msg = enforce_tier_limits(
                    tier=tier,
                    job_type=job_type,
                )
                if not allowed:
                    logger.error(
                        "Tier limit violation: job_id=%s, tier=%s, job_type=%s, error=%s",
                        job_id,
                        tier,
                        job_type,
                        error_msg,
                    )
                    queue.fail_job(
                        job_id=job_id,
                        error_message=error_msg or "Tier limit violation",
                    )
                    queue.delete_message(receipt_handle, tier=tier)
                    continue
                
                # 작업을 RUNNING 상태로 변경 (멱등성 보장)
                if not queue.mark_processing(job_id):
                    logger.warning(
                        "Cannot mark AI job %s as RUNNING, skipping",
                        job_id,
                    )
                    # 상태 변경 실패 시 메시지 삭제 (재시도하지 않음)
                    queue.delete_message(receipt_handle, tier=tier)
                    continue
                
                # AIJob 객체 생성
                job = AIJob(
                    id=job_id,
                    type=job_type,
                    payload=payload,
                    source_id=message.get("source_id"),
                )
                
                # AI 작업 처리 (GPU 사용)
                try:
                    processing_start = time.time()
                    result = handle_ai_job(job)
                    processing_duration = time.time() - processing_start
                    
                    # 완료 처리
                    complete_start = time.time()
                    ok, reason = queue.complete_job(
                        job_id=job_id,
                        result_payload=result.result if isinstance(result.result, dict) else {},
                    )
                    complete_duration = time.time() - complete_start
                    
                    if not ok:
                        logger.error(
                            "SQS_JOB_COMPLETE_FAILED | request_id=%s | job_id=%s | tier=%s | reason=%s | processing_duration=%.2f",
                            request_id,
                            job_id,
                            tier,
                            reason,
                            processing_duration,
                        )
                        # 메시지는 삭제하지 않음 (재시도)
                        _current_job_receipt_handle = None
                        _current_job_start_time = None
                        continue
                    
                    # 성공 시 메시지 삭제
                    queue.delete_message(receipt_handle, tier=tier)
                    total_duration = time.time() - _current_job_start_time
                    
                    # 로그 가시성: 전체 처리 시간 추적
                    logger.info(
                        "SQS_JOB_COMPLETED | request_id=%s | job_id=%s | tier=%s | status=%s | processing_duration=%.2f | complete_duration=%.2f | total_duration=%.2f | queue_wait_sec=%.2f",
                        request_id,
                        job_id,
                        tier,
                        result.status,
                        processing_duration,
                        complete_duration,
                        total_duration,
                        queue_wait_time,
                    )
                    consecutive_errors = 0
                    
                    # Graceful shutdown: 작업 완료
                    _current_job_receipt_handle = None
                    _current_job_start_time = None
                    
                    # 종료 신호를 받았으면 루프 종료
                    if _shutdown:
                        logger.info("Graceful shutdown: current job completed, exiting")
                        break
                    
                except Exception as e:
                    processing_duration = time.time() - _current_job_start_time if _current_job_start_time else 0
                    
                    logger.exception(
                        "SQS_JOB_FAILED | request_id=%s | job_id=%s | tier=%s | error=%s | processing_duration=%.2f | queue_wait_sec=%.2f",
                        request_id,
                        job_id,
                        tier,
                        str(e)[:200],
                        processing_duration,
                        queue_wait_time,
                    )
                    
                    # 실패 처리 (작업 상태를 FAILED로 변경)
                    queue.fail_job(
                        job_id=job_id,
                        error_message=str(e)[:2000],
                    )
                    
                    # SQS Visibility Timeout 확인: 처리 시간이 timeout을 초과하면 메시지가 다시 보임
                    if processing_duration > SQS_VISIBILITY_TIMEOUT:
                        logger.warning(
                            "SQS_VISIBILITY_TIMEOUT_EXCEEDED | request_id=%s | job_id=%s | processing_duration=%.2f | visibility_timeout=%d | message_will_reappear",
                            request_id,
                            job_id,
                            processing_duration,
                            SQS_VISIBILITY_TIMEOUT,
                        )
                    
                    # 메시지는 삭제하지 않음 (SQS가 자동으로 재시도)
                    # 재시도 횟수 초과 시 자동으로 DLQ로 이동
                    consecutive_errors += 1
                    
                    # Graceful shutdown: 작업 실패 처리 완료
                    _current_job_receipt_handle = None
                    _current_job_start_time = None
                    
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
        
        logger.info("AI Worker GPU shutdown complete")
        return 0
        
    except Exception:
        logger.exception("Fatal error in AI Worker GPU")
        return 1


if __name__ == "__main__":
    sys.exit(main())
