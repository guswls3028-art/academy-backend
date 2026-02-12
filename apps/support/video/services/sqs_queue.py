"""
SQS 기반 Video Job Queue

기존 VideoJobQueue (DB 기반)를 SQS로 교체
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.support.video.models import Video
from libs.queue import get_queue_client

logger = logging.getLogger(__name__)


class VideoSQSQueue:
    """
    SQS 기반 Video Job Queue
    
    메시지 형식:
    {
        "video_id": int,
        "file_key": str,
        "tenant_code": str,
        "created_at": "ISO8601",
        "attempt": int  # 재시도 횟수
    }
    """

    QUEUE_NAME = "academy-video-jobs"
    DLQ_NAME = "academy-video-jobs-dlq"
    
    # 메시지 속성
    MAX_RECEIVE_COUNT = 3  # DLQ로 전송 전 최대 재시도 횟수
    
    def __init__(self):
        self.queue_client = get_queue_client()
    
    def _get_queue_name(self) -> str:
        """환경변수로 오버라이드 가능"""
        return getattr(settings, "VIDEO_SQS_QUEUE_NAME", self.QUEUE_NAME)
    
    def _get_dlq_name(self) -> str:
        """환경변수로 오버라이드 가능"""
        return getattr(settings, "VIDEO_SQS_DLQ_NAME", self.DLQ_NAME)
    
    def enqueue(self, video: Video) -> bool:
        """
        비디오 작업을 SQS에 추가
        
        Args:
            video: Video 객체 (status가 UPLOADED여야 함)
            
        Returns:
            bool: 성공 여부
        """
        if video.status != Video.Status.UPLOADED:
            logger.warning(
                "Cannot enqueue video %s: status=%s (expected UPLOADED)",
                video.id,
                video.status,
            )
            return False
        
        # tenant_code 가져오기
        try:
            tenant_code = video.session.lecture.tenant.code
        except Exception:
            logger.error("Cannot get tenant_code for video %s", video.id)
            return False
        
        message = {
            "video_id": int(video.id),
            "file_key": str(video.file_key or ""),
            "tenant_code": str(tenant_code),
            "created_at": timezone.now().isoformat(),
            "attempt": 1,
        }
        
        try:
            success = self.queue_client.send_message(
                queue_name=self._get_queue_name(),
                message=message,
            )
            
            if success:
                logger.info("Video job enqueued: video_id=%s", video.id)
            else:
                logger.error("Failed to enqueue video job: video_id=%s", video.id)
            
            return success
            
        except Exception as e:
            logger.exception("Error enqueuing video job: video_id=%s, error=%s", video.id, e)
            return False
    
    def receive_message(self, wait_time_seconds: int = 20) -> Optional[dict]:
        """
        SQS에서 메시지 수신 (Long Polling)
        
        Args:
            wait_time_seconds: Long Polling 대기 시간 (최대 20초)
            
        Returns:
            dict: 메시지 (video_id, file_key, tenant_code, receipt_handle 포함) 또는 None
        """
        try:
            message = self.queue_client.receive_message(
                queue_name=self._get_queue_name(),
                wait_time_seconds=wait_time_seconds,
            )
            
            if not message:
                return None
            
            # SQS 메시지 형식에 따라 파싱
            body = message.get("Body", "")
            receipt_handle = message.get("ReceiptHandle")
            
            # Body는 항상 JSON 문자열
            if isinstance(body, str):
                try:
                    job_data = json.loads(body)
                except json.JSONDecodeError:
                    logger.error("Invalid JSON in message: %s", body)
                    return None
            else:
                job_data = body
            
            # 메시지 형식 검증
            if not isinstance(job_data, dict) or "video_id" not in job_data:
                logger.error("Invalid message format: %s", job_data)
                return None
            
            # ReceiptHandle 필수 (SQS)
            if not receipt_handle:
                logger.error("Missing ReceiptHandle in SQS message")
                return None
            
            # 작업 데이터 반환 (로그 가시성: created_at 포함)
            return {
                "video_id": int(job_data.get("video_id")),
                "file_key": str(job_data.get("file_key", "")),
                "tenant_code": str(job_data.get("tenant_code", "")),
                "receipt_handle": receipt_handle,
                "message_id": message.get("MessageId"),
                "created_at": job_data.get("created_at"),  # SQS 메시지 수명 추적용
            }
            
        except Exception as e:
            logger.exception("Error receiving message from SQS: %s", e)
            return None
    
    def delete_message(self, receipt_handle: str) -> bool:
        """
        처리 완료된 메시지 삭제
        
        Args:
            receipt_handle: SQS 메시지 ReceiptHandle
            
        Returns:
            bool: 성공 여부
        """
        try:
            return self.queue_client.delete_message(
                queue_name=self._get_queue_name(),
                receipt_handle=receipt_handle,
            )
        except Exception as e:
            logger.exception("Error deleting message: receipt_handle=%s, error=%s", receipt_handle, e)
            return False
    
    def mark_failed(self, receipt_handle: str, reason: str) -> bool:
        """
        실패한 메시지 처리
        
        SQS의 자동 DLQ 전송을 사용하므로, 여기서는 메시지만 삭제
        (재시도 횟수 초과 시 자동으로 DLQ로 이동)
        
        또는 수동으로 DLQ에 전송할 수도 있음
        
        Args:
            receipt_handle: SQS 메시지 ReceiptHandle
            reason: 실패 사유
            
        Returns:
            bool: 성공 여부
        """
        # SQS는 자동으로 재시도 횟수 초과 시 DLQ로 전송
        # 여기서는 메시지를 삭제하지 않고 그대로 두면 자동 재시도됨
        # 또는 즉시 실패 처리하려면 메시지를 삭제
        
        # 현재는 메시지를 삭제하여 재시도하지 않도록 함
        # (재시도는 SQS 레벨에서 처리)
        logger.warning("Video job failed: receipt_handle=%s, reason=%s", receipt_handle, reason)
        return True
    
    @transaction.atomic
    def complete_video(
        self,
        video_id: int,
        hls_path: str,
        duration: Optional[int] = None,
    ) -> tuple[bool, str]:
        """
        비디오 처리 완료 처리
        
        Args:
            video_id: Video ID
            hls_path: HLS 마스터 플레이리스트 경로
            duration: 비디오 길이 (초)
            
        Returns:
            tuple[bool, str]: (성공 여부, 이유)
        """
        video = Video.objects.select_for_update().filter(id=int(video_id)).first()
        if not video:
            return False, "not_found"
        
        # 멱등성: 이미 READY 상태면 OK
        if video.status == Video.Status.READY and bool(video.hls_path):
            return True, "idempotent"
        
        # 상태가 PROCESSING이 아니면 경고
        if video.status != Video.Status.PROCESSING:
            logger.warning(
                "Video %s status is %s (expected PROCESSING)",
                video_id,
                video.status,
            )
        
        video.hls_path = str(hls_path)
        if duration is not None and duration >= 0:
            video.duration = int(duration)
        video.status = Video.Status.READY
        
        # lease 해제
        if hasattr(video, "leased_until"):
            video.leased_until = None
        if hasattr(video, "leased_by"):
            video.leased_by = ""
        
        update_fields = ["hls_path", "status"]
        if duration is not None and duration >= 0:
            update_fields.append("duration")
        if hasattr(video, "leased_until"):
            update_fields.append("leased_until")
        if hasattr(video, "leased_by"):
            update_fields.append("leased_by")
        
        video.save(update_fields=update_fields)
        return True, "ok"
    
    @transaction.atomic
    def fail_video(
        self,
        video_id: int,
        reason: str,
    ) -> tuple[bool, str]:
        """
        비디오 처리 실패 처리
        
        Args:
            video_id: Video ID
            reason: 실패 사유
            
        Returns:
            tuple[bool, str]: (성공 여부, 이유)
        """
        video = Video.objects.select_for_update().filter(id=int(video_id)).first()
        if not video:
            return False, "not_found"
        
        # 멱등성: 이미 FAILED 상태면 OK
        if video.status == Video.Status.FAILED:
            return True, "idempotent"
        
        video.status = Video.Status.FAILED
        if hasattr(video, "error_reason"):
            video.error_reason = str(reason)[:2000]
        
        # lease 해제
        if hasattr(video, "leased_until"):
            video.leased_until = None
        if hasattr(video, "leased_by"):
            video.leased_by = ""
        
        update_fields = ["status"]
        if hasattr(video, "error_reason"):
            update_fields.append("error_reason")
        if hasattr(video, "leased_until"):
            update_fields.append("leased_until")
        if hasattr(video, "leased_by"):
            update_fields.append("leased_by")
        
        video.save(update_fields=update_fields)
        return True, "ok"
    
    @transaction.atomic
    def mark_processing(self, video_id: int) -> bool:
        """
        비디오를 PROCESSING 상태로 변경 (멱등성 보장)
        
        Args:
            video_id: Video ID
            
        Returns:
            bool: 성공 여부
        """
        video = Video.objects.select_for_update().filter(id=int(video_id)).first()
        if not video:
            return False
        
        # 이미 PROCESSING이면 OK
        if video.status == Video.Status.PROCESSING:
            return True
        
        # UPLOADED 상태만 PROCESSING으로 변경 가능
        if video.status != Video.Status.UPLOADED:
            logger.warning(
                "Cannot mark video %s as PROCESSING: status=%s",
                video_id,
                video.status,
            )
            return False
        
        video.status = Video.Status.PROCESSING
        if hasattr(video, "processing_started_at"):
            video.processing_started_at = timezone.now()
        
        update_fields = ["status"]
        if hasattr(video, "processing_started_at"):
            update_fields.append("processing_started_at")
        
        video.save(update_fields=update_fields)
        return True
