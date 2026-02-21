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

from academy.adapters.db.django.repositories_video import get_video_for_update
from apps.support.video.models import Video
from libs.queue import get_queue_client, QueueUnavailableError

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
        # [TRACE] enqueue entry (enqueue_video_job equivalent)
        _tid = getattr(getattr(getattr(video, "session", None), "lecture", None), "tenant_id", None)
        logger.info(
            "VIDEO_UPLOAD_TRACE | enqueue entry | video_id=%s tenant_id=%s source_path=%s status=%s execution=3_ENQUEUE_ENTRY",
            video.id,
            _tid,
            video.file_key or "",
            video.status,
        )
        if video.status != Video.Status.UPLOADED:
            logger.warning(
                "Cannot enqueue video %s: status=%s (expected UPLOADED)",
                video.id,
                video.status,
            )
            return False
        
        # tenant (경로 통일: tenants/{tenant_id}/...)
        try:
            tenant = video.session.lecture.tenant
            tenant_id = int(tenant.id)
            tenant_code = tenant.code
        except Exception:
            logger.error("Cannot get tenant for video %s", video.id)
            return False

        message = {
            "video_id": int(video.id),
            "file_key": str(video.file_key or ""),
            "tenant_id": tenant_id,
            "tenant_code": str(tenant_code),
            "created_at": timezone.now().isoformat(),
            "attempt": 1,
        }
        
        try:
            logger.info(
                "VIDEO_UPLOAD_TRACE | calling send_message | video_id=%s tenant_id=%s source_path=%s execution=4_SEND_MESSAGE_CALL",
                video.id, tenant_id, video.file_key or "",
            )
            success = self.queue_client.send_message(
                queue_name=self._get_queue_name(),
                message=message,
            )
            logger.info(
                "VIDEO_UPLOAD_TRACE | send_message returned | video_id=%s success=%s execution=5_SEND_MESSAGE_DONE",
                video.id, success,
            )
            if success:
                logger.info("Video job enqueued: video_id=%s", video.id)
            else:
                logger.error("Failed to enqueue video job: video_id=%s", video.id)

            return success

        except Exception as e:
            logger.exception(
                "VIDEO_UPLOAD_TRACE | enqueue exception (exposed) | video_id=%s tenant_id=%s error=%s execution=ERR_ENQUEUE",
                video.id, tenant_id, e,
            )
            return False

    def create_job_and_enqueue(self, video: Video) -> Optional["VideoTranscodeJob"]:
        """
        Job 생성 + enqueue. upload_complete, retry에서 사용.
        video.status must be UPLOADED.
        """
        from apps.support.video.models import VideoTranscodeJob

        if video.status != Video.Status.UPLOADED:
            logger.warning("create_job_and_enqueue: video %s status=%s (expected UPLOADED)", video.id, video.status)
            return None
        try:
            tenant = video.session.lecture.tenant
            tenant_id = int(tenant.id)
        except Exception:
            logger.error("Cannot get tenant for video %s", video.id)
            return None

        job = VideoTranscodeJob.objects.create(
            video=video,
            tenant_id=tenant_id,
            state=VideoTranscodeJob.State.QUEUED,
        )
        video.current_job_id = job.id
        video.save(update_fields=["current_job_id", "updated_at"])

        if not self.enqueue_by_job(job):
            job.delete()
            video.current_job_id = None
            video.save(update_fields=["current_job_id", "updated_at"])
            return None
        return job

    def enqueue_by_job(self, job) -> bool:
        """
        Job 기반 SQS enqueue. 메시지에 job_id 포함 (DLQ 추적용).

        Args:
            job: VideoTranscodeJob 객체 (video, tenant_id, id 필요)

        Returns:
            bool: 성공 여부
        """
        from apps.support.video.models import VideoTranscodeJob

        if not isinstance(job, VideoTranscodeJob):
            logger.error("enqueue_by_job: job must be VideoTranscodeJob, got %s", type(job))
            return False
        video = job.video
        video_id = int(video.id)
        tenant_id = int(job.tenant_id)
        file_key = str(video.file_key or "")

        message = {
            "job_id": str(job.id),
            "video_id": video_id,
            "tenant_id": tenant_id,
            "file_key": file_key,
        }
        try:
            success = self.queue_client.send_message(
                queue_name=self._get_queue_name(),
                message=message,
            )
            if success:
                from apps.support.video.redis_status_cache import redis_incr_video_backlog
                redis_incr_video_backlog(tenant_id)
                logger.info(
                    "Video job enqueued | job_id=%s | video_id=%s | tenant_id=%s",
                    job.id, video_id, tenant_id,
                )
            else:
                logger.error("Failed to enqueue video job | job_id=%s | video_id=%s", job.id, video_id)
            return bool(success)
        except Exception as e:
            logger.exception(
                "enqueue_by_job exception | job_id=%s video_id=%s error=%s",
                job.id, video_id, e,
            )
            return False

    def enqueue_delete_r2(
        self,
        *,
        tenant_id: int,
        video_id: int,
        file_key: str,
        hls_prefix: str,
    ) -> bool:
        """
        영상 삭제 후 R2 정리를 워커에 위임 (비동기).
        API에서 DB 삭제 직후 호출. 동일 Video 큐에 delete_r2 메시지 발행.
        """
        message = {
            "action": "delete_r2",
            "tenant_id": tenant_id,
            "video_id": video_id,
            "file_key": (file_key or "").strip(),
            "hls_prefix": hls_prefix,
            "created_at": timezone.now().isoformat(),
        }
        try:
            success = self.queue_client.send_message(
                queue_name=self._get_queue_name(),
                message=message,
            )
            if success:
                logger.info("R2 delete job enqueued: video_id=%s hls_prefix=%s", video_id, hls_prefix)
            return bool(success)
        except Exception as e:
            logger.exception("Error enqueuing R2 delete job: video_id=%s, error=%s", video_id, e)
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
            if not isinstance(job_data, dict):
                logger.error("Invalid message format: %s", job_data)
                return None

            # R2 삭제 작업 (비동기 삭제용)
            if job_data.get("action") == "delete_r2":
                if not receipt_handle:
                    logger.error("Missing ReceiptHandle in SQS message")
                    return None
                return {
                    "action": "delete_r2",
                    "tenant_id": int(job_data["tenant_id"]),
                    "video_id": int(job_data["video_id"]),
                    "file_key": str(job_data.get("file_key", "")),
                    "hls_prefix": str(job_data.get("hls_prefix", "")),
                    "receipt_handle": receipt_handle,
                    "message_id": message.get("MessageId"),
                }

            # 인코딩 작업: job_id (필수), video_id 필수
            if "video_id" not in job_data:
                logger.error("Invalid message format (video_id required): %s", job_data)
                return None

            job_id_raw = job_data.get("job_id")
            job_id = str(job_id_raw) if job_id_raw is not None else None

            # ReceiptHandle 필수 (SQS)
            if not receipt_handle:
                logger.error("Missing ReceiptHandle in SQS message")
                return None

            tenant_code = str(job_data.get("tenant_code", ""))

            # 작업 데이터 반환 (job_id: Job 기반 처리용, DLQ 추적)
            return {
                "job_id": job_id,
                "video_id": int(job_data.get("video_id")),
                "file_key": str(job_data.get("file_key", "")),
                "tenant_id": int(job_data["tenant_id"]) if job_data.get("tenant_id") is not None else None,
                "tenant_code": tenant_code,
                "receipt_handle": receipt_handle,
                "message_id": message.get("MessageId"),
                "created_at": job_data.get("created_at"),
            }
            
        except QueueUnavailableError:
            raise
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

    def change_message_visibility(self, receipt_handle: str, visibility_timeout: int = 10800) -> bool:
        """
        장시간 작업 시 메시지 재노출 방지 (ChangeMessageVisibility).
        인코딩 시작 직후 호출 권장.
        """
        try:
            return self.queue_client.change_message_visibility(
                queue_name=self._get_queue_name(),
                receipt_handle=receipt_handle,
                visibility_timeout=visibility_timeout,
            )
        except Exception as e:
            logger.warning("ChangeMessageVisibility failed (continuing): %s", e)
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
        video = get_video_for_update(video_id)
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
        
        # ✅ Redis에 최종 상태 저장 (TTL 없음)
        try:
            from apps.support.video.redis_status_cache import cache_video_status
            # tenant_id는 video에서 가져오기 (select_related로 이미 로드됨)
            tenant_id = None
            if hasattr(video, "session") and video.session:
                if hasattr(video.session, "lecture") and video.session.lecture:
                    tenant_id = video.session.lecture.tenant_id
            
            if tenant_id:
                # ✅ 안전한 Status 값 추출 (TextChoices이면 .value, 아니면 그대로)
                status_value = getattr(Video.Status.READY, "value", Video.Status.READY)
                cache_video_status(
                    tenant_id=tenant_id,
                    video_id=video_id,
                    status=status_value,
                    hls_path=str(hls_path),
                    duration=duration,
                    ttl=None,  # TTL 없음
                )
        except Exception as e:
            logger.warning("Failed to cache video status in Redis: %s", e)
        
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
        video = get_video_for_update(video_id)
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
        
        # ✅ Redis에 실패 상태 저장 (TTL 없음)
        try:
            from apps.support.video.redis_status_cache import cache_video_status
            # tenant_id는 video에서 가져오기 (select_related로 이미 로드됨)
            tenant_id = None
            if hasattr(video, "session") and video.session:
                if hasattr(video.session, "lecture") and video.session.lecture:
                    tenant_id = video.session.lecture.tenant_id
            
            if tenant_id:
                # ✅ 안전한 Status 값 추출 (TextChoices이면 .value, 아니면 그대로)
                status_value = getattr(Video.Status.FAILED, "value", Video.Status.FAILED)
                cache_video_status(
                    tenant_id=tenant_id,
                    video_id=video_id,
                    status=status_value,
                    error_reason=str(reason)[:2000],
                    ttl=None,  # TTL 없음
                )
        except Exception as e:
            logger.warning("Failed to cache video status in Redis: %s", e)
        
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
        video = get_video_for_update(video_id)
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
        
        # ✅ Redis에 PROCESSING 상태 저장 (TTL 6시간)
        try:
            from apps.support.video.redis_status_cache import cache_video_status
            # tenant_id는 video에서 가져오기 (select_related로 이미 로드됨)
            tenant_id = None
            if hasattr(video, "session") and video.session:
                if hasattr(video.session, "lecture") and video.session.lecture:
                    tenant_id = video.session.lecture.tenant_id
            
            if tenant_id:
                # ✅ 안전한 Status 값 추출 (TextChoices이면 .value, 아니면 그대로)
                status_value = getattr(Video.Status.PROCESSING, "value", Video.Status.PROCESSING)
                cache_video_status(
                    tenant_id=tenant_id,
                    video_id=video_id,
                    status=status_value,
                    ttl=21600,  # 6시간
                )
        except Exception as e:
            logger.warning("Failed to cache video status in Redis: %s", e)
        
        return True
