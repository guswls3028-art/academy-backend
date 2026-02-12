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
from apps.worker.video_worker.video.processor import process_video_job
from apps.support.video.services.sqs_queue import VideoSQSQueue

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
    queue = VideoSQSQueue()
    
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
                    # 잘못된 메시지는 삭제하여 DLQ로 이동하지 않도록
                    queue.delete_message(receipt_handle)
                    continue
                
                # 로그 가시성: request_id 생성 및 메시지 수명 추적
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
                
                # Graceful shutdown: 현재 작업 추적 시작
                global _current_job_receipt_handle, _current_job_start_time
                _current_job_receipt_handle = receipt_handle
                _current_job_start_time = time.time()
                
                # 비디오를 PROCESSING 상태로 변경 (멱등성 보장)
                if not queue.mark_processing(int(video_id)):
                    logger.warning(
                        "Cannot mark video %s as PROCESSING, skipping",
                        video_id,
                    )
                    # 상태 변경 실패 시 메시지 삭제 (재시도하지 않음)
                    queue.delete_message(receipt_handle)
                    continue
                
                # 작업 데이터 구성
                job = {
                    "video_id": int(video_id),
                    "file_key": str(file_key or ""),
                    "tenant_code": str(tenant_code),
                }
                
                # 비디오 처리
                try:
                    processing_start = time.time()
                    # 기존 processor 사용 (HTTP client 대신 SQS queue 사용)
                    process_video_job_sqs(
                        job=job,
                        cfg=cfg,
                        queue=queue,
                    )
                    processing_duration = time.time() - processing_start
                    
                    # 성공 시 메시지 삭제
                    complete_start = time.time()
                    queue.delete_message(receipt_handle)
                    complete_duration = time.time() - complete_start
                    total_duration = time.time() - _current_job_start_time
                    
                    # 로그 가시성: 전체 처리 시간 추적
                    logger.info(
                        "SQS_JOB_COMPLETED | request_id=%s | video_id=%s | tenant_code=%s | processing_duration=%.2f | complete_duration=%.2f | total_duration=%.2f | queue_wait_sec=%.2f",
                        request_id,
                        video_id,
                        tenant_code,
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
                        "SQS_JOB_FAILED | request_id=%s | video_id=%s | tenant_code=%s | error=%s | processing_duration=%.2f | queue_wait_sec=%.2f",
                        request_id,
                        video_id,
                        tenant_code,
                        str(e)[:200],
                        processing_duration,
                        queue_wait_time,
                    )
                    
                    # 실패 처리 (비디오 상태를 FAILED로 변경)
                    queue.fail_video(
                        video_id=int(video_id),
                        reason=str(e)[:2000],
                    )
                    
                    # SQS Visibility Timeout 확인: 처리 시간이 timeout을 초과하면 메시지가 다시 보임
                    if processing_duration > SQS_VISIBILITY_TIMEOUT:
                        logger.warning(
                            "SQS_VISIBILITY_TIMEOUT_EXCEEDED | request_id=%s | video_id=%s | processing_duration=%.2f | visibility_timeout=%d | message_will_reappear",
                            request_id,
                            video_id,
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
        
        logger.info("Video Worker shutdown complete")
        return 0
        
    except Exception:
        logger.exception("Fatal error in Video Worker")
        return 1


def process_video_job_sqs(
    *,
    job: dict,
    cfg,
    queue: VideoSQSQueue,
) -> None:
    """
    SQS 기반 비디오 작업 처리
    
    기존 process_video_job과 유사하지만 HTTP client 대신 SQS queue 사용
    """
    """
    SQS 기반 비디오 작업 처리
    
    기존 process_video_job과 유사하지만 HTTP client 대신 SQS queue 사용
    """
    from pathlib import Path
    from apps.worker.video_worker.download import download_to_file
    from apps.worker.video_worker.utils import temp_workdir, trim_tail
    from apps.worker.video_worker.video.duration import probe_duration_seconds
    from apps.worker.video_worker.video.thumbnail import generate_thumbnail
    from apps.worker.video_worker.video.transcoder import transcode_to_hls
    from apps.worker.video_worker.video.validate import validate_hls_output
    from apps.worker.video_worker.video.r2_uploader import upload_directory
    
    video_id = int(job.get("video_id"))
    file_key = str(job.get("file_key") or "")
    tenant_code = str(job.get("tenant_code") or "")
    
    if not video_id or not tenant_code:
        raise ValueError("video_id and tenant_code required")
    
    # Source URL 생성
    try:
        from libs.s3_client.presign import create_presigned_get_url
        source_url = create_presigned_get_url(key=file_key, expires_in=600)
    except Exception as e:
        raise RuntimeError(f"presigned_get_failed:{trim_tail(str(e))}") from e
    
    # HLS 경로 생성
    base = (cfg.R2_PREFIX or "media/hls").strip("/")
    hls_prefix = f"{base}/{tenant_code}/videos/{video_id}"
    hls_master_path = f"{hls_prefix}/master.m3u8"
    
    # 작업 디렉토리에서 처리
    with temp_workdir(cfg.TEMP_DIR, prefix=f"video-{video_id}-") as wd:
        wd = Path(wd)
        src_path = wd / "source.mp4"
        out_dir = wd / "hls"
        
        # 1) 다운로드
        download_to_file(url=source_url, dst=src_path, cfg=cfg)
        
        # 2) Duration 추출
        duration = probe_duration_seconds(
            input_path=str(src_path),
            ffprobe_bin=cfg.FFPROBE_BIN,
            timeout=int(cfg.FFPROBE_TIMEOUT_SECONDS),
        )
        if not duration or duration <= 0:
            raise RuntimeError("duration_probe_failed")
        
        # 3) Transcode to HLS
        transcode_to_hls(
            video_id=video_id,
            input_path=str(src_path),
            output_root=out_dir,
            ffmpeg_bin=cfg.FFMPEG_BIN,
            ffprobe_bin=cfg.FFPROBE_BIN,
            hls_time=int(cfg.HLS_TIME_SECONDS),
            timeout=int(cfg.FFMPEG_TIMEOUT_SECONDS),
        )
        
        # 4) Validate
        validate_hls_output(out_dir, int(cfg.MIN_SEGMENTS_PER_VARIANT))
        
        # 5) Thumbnail 생성
        try:
            at = float(cfg.THUMBNAIL_AT_SECONDS)
            if duration >= 10:
                at = float(int(duration * 0.5))
            elif duration >= 3:
                at = float(max(1, duration // 2))
            else:
                at = 0.0
            
            thumb_path = out_dir / "thumbnail.jpg"
            generate_thumbnail(
                input_path=str(src_path),
                output_path=thumb_path,
                ffmpeg_bin=cfg.FFMPEG_BIN,
                at_seconds=float(at),
                timeout=min(int(cfg.FFMPEG_TIMEOUT_SECONDS), 120),
            )
        except Exception as e:
            logger.warning("thumbnail failed video_id=%s err=%s", video_id, e)
        
        # 6) R2에 업로드
        upload_directory(
            local_dir=out_dir,
            bucket=cfg.R2_BUCKET,
            prefix=hls_prefix,
            endpoint_url=cfg.R2_ENDPOINT,
            access_key=cfg.R2_ACCESS_KEY,
            secret_key=cfg.R2_SECRET_KEY,
            region=cfg.R2_REGION,
            max_concurrency=int(cfg.UPLOAD_MAX_CONCURRENCY),
            retry_max=int(cfg.RETRY_MAX_ATTEMPTS),
            backoff_base=float(cfg.BACKOFF_BASE_SECONDS),
            backoff_cap=float(cfg.BACKOFF_CAP_SECONDS),
        )
    
    # 7) 완료 처리 (SQS queue 사용)
    ok, reason = queue.complete_video(
        video_id=video_id,
        hls_path=hls_master_path,
        duration=int(duration),
    )
    
    if not ok:
        raise RuntimeError(f"Failed to complete video: {reason}")
    
    logger.info("Video processing completed: video_id=%s, duration=%s", video_id, duration)


if __name__ == "__main__":
    sys.exit(main())
