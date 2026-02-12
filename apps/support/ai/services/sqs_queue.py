"""
SQS 기반 AI Job Queue

기존 HTTP polling + DB queue를 SQS로 교체
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.domains.ai.models import AIJobModel
from libs.queue import get_queue_client

logger = logging.getLogger(__name__)


class AISQSQueue:
    """
    SQS 기반 AI Job Queue (3-Tier 시스템)
    
    Tier별 큐:
    - lite: ai-lite-queue (CPU)
    - basic: ai-basic-queue (CPU)
    - premium: ai-premium-queue (GPU, 향후)
    
    메시지 형식:
    {
        "job_id": str,
        "job_type": str,
        "tier": "lite" | "basic" | "premium",
        "payload": dict,
        "tenant_id": str (optional),
        "source_domain": str (optional),
        "source_id": str (optional),
        "created_at": "ISO8601",
        "attempt": int
    }
    """

    QUEUE_NAME_LITE = "academy-ai-jobs-lite"
    QUEUE_NAME_BASIC = "academy-ai-jobs-basic"
    QUEUE_NAME_PREMIUM = "academy-ai-jobs-premium"
    DLQ_NAME_LITE = "academy-ai-jobs-lite-dlq"
    DLQ_NAME_BASIC = "academy-ai-jobs-basic-dlq"
    DLQ_NAME_PREMIUM = "academy-ai-jobs-premium-dlq"
    
    MAX_RECEIVE_COUNT = 3  # DLQ로 전송 전 최대 재시도 횟수
    
    def __init__(self):
        self.queue_client = get_queue_client()
    
    def _get_queue_name(self, tier: str) -> str:
        """
        Tier별 큐 이름 반환
        
        Args:
            tier: "lite" | "basic" | "premium"
            
        Returns:
            str: 큐 이름
        """
        tier = tier.lower()
        if tier == "lite":
            return getattr(settings, "AI_SQS_QUEUE_NAME_LITE", self.QUEUE_NAME_LITE)
        elif tier == "basic":
            return getattr(settings, "AI_SQS_QUEUE_NAME_BASIC", self.QUEUE_NAME_BASIC)
        elif tier == "premium":
            return getattr(settings, "AI_SQS_QUEUE_NAME_PREMIUM", self.QUEUE_NAME_PREMIUM)
        else:
            # 기본값: basic
            logger.warning("Unknown tier %s, using basic queue", tier)
            return getattr(settings, "AI_SQS_QUEUE_NAME_BASIC", self.QUEUE_NAME_BASIC)
    
    def _get_dlq_name(self, tier: str) -> str:
        """
        Tier별 DLQ 이름 반환
        
        Args:
            tier: "lite" | "basic" | "premium"
            
        Returns:
            str: DLQ 이름
        """
        tier = tier.lower()
        if tier == "lite":
            return getattr(settings, "AI_SQS_DLQ_NAME_LITE", self.DLQ_NAME_LITE)
        elif tier == "basic":
            return getattr(settings, "AI_SQS_DLQ_NAME_BASIC", self.DLQ_NAME_BASIC)
        elif tier == "premium":
            return getattr(settings, "AI_SQS_DLQ_NAME_PREMIUM", self.DLQ_NAME_PREMIUM)
        else:
            # 기본값: basic
            return getattr(settings, "AI_SQS_DLQ_NAME_BASIC", self.DLQ_NAME_BASIC)
    
    def enqueue(self, job: AIJobModel) -> bool:
        """
        AI 작업을 Tier별 SQS 큐에 추가
        
        Args:
            job: AIJobModel 객체 (tier 필드 필수)
            
        Returns:
            bool: 성공 여부
        """
        # Status는 문자열로 비교
        if job.status != "PENDING":
            logger.warning(
                "Cannot enqueue AI job %s: status=%s (expected PENDING)",
                job.job_id,
                job.status,
            )
            return False
        
        # Tier 확인 (기본값: basic)
        tier = (job.tier or "basic").lower()
        if tier not in ("lite", "basic", "premium"):
            logger.warning("Invalid tier %s for job %s, using basic", tier, job.job_id)
            tier = "basic"
        
        message = {
            "job_id": str(job.job_id),
            "job_type": str(job.job_type),
            "tier": tier,
            "payload": job.payload or {},
            "tenant_id": str(job.tenant_id) if job.tenant_id else None,
            "source_domain": str(job.source_domain) if job.source_domain else None,
            "source_id": str(job.source_id) if job.source_id else None,
            "created_at": timezone.now().isoformat(),
            "attempt": 1,
        }
        
        queue_name = self._get_queue_name(tier=tier)
        
        try:
            success = self.queue_client.send_message(
                queue_name=queue_name,
                message=message,
            )
            
            if success:
                logger.info(
                    "AI job enqueued: job_id=%s, tier=%s, queue=%s",
                    job.job_id,
                    tier,
                    queue_name,
                )
            else:
                logger.error("Failed to enqueue AI job: job_id=%s, tier=%s", job.job_id, tier)
            
            return success
            
        except Exception as e:
            logger.exception("Error enqueuing AI job: job_id=%s, tier=%s, error=%s", job.job_id, tier, e)
            return False
    
    def receive_message(
        self,
        tier: Optional[str] = None,
        wait_time_seconds: int = 20,
    ) -> Optional[dict]:
        """
        SQS에서 메시지 수신 (Long Polling)
        
        Args:
            tier: 큐에서 수신할 tier ("lite" | "basic" | "premium")
            wait_time_seconds: Long Polling 대기 시간 (최대 20초)
            
        Returns:
            dict: 메시지 (job_id, job_type, tier, payload 등 포함) 또는 None
        """
        if not tier:
            raise ValueError("tier parameter is required")
        
        queue_name = self._get_queue_name(tier=tier)
        
        try:
            message = self.queue_client.receive_message(
                queue_name=queue_name,
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
            if not isinstance(job_data, dict) or "job_id" not in job_data:
                logger.error("Invalid message format: %s", job_data)
                return None
            
            # ReceiptHandle 필수 (SQS)
            if not receipt_handle:
                logger.error("Missing ReceiptHandle in SQS message")
                return None
            
            # 작업 데이터 반환 (로그 가시성: created_at 포함)
            return {
                "job_id": str(job_data.get("job_id")),
                "job_type": str(job_data.get("job_type", "")),
                "tier": str(job_data.get("tier", "basic")),
                "payload": job_data.get("payload", {}),
                "tenant_id": job_data.get("tenant_id"),
                "source_domain": job_data.get("source_domain"),
                "source_id": job_data.get("source_id"),
                "receipt_handle": receipt_handle,
                "message_id": message.get("MessageId"),
                "created_at": job_data.get("created_at"),  # SQS 메시지 수명 추적용
            }
            
        except Exception as e:
            logger.exception("Error receiving message from SQS: %s", e)
            return None
    
    def delete_message(self, receipt_handle: str, tier: str) -> bool:
        """
        처리 완료된 메시지 삭제
        
        Args:
            receipt_handle: SQS 메시지 ReceiptHandle
            tier: 큐 tier ("lite" | "basic" | "premium")
            
        Returns:
            bool: 성공 여부
        """
        queue_name = self._get_queue_name(tier=tier)
        
        try:
            return self.queue_client.delete_message(
                queue_name=queue_name,
                receipt_handle=receipt_handle,
            )
        except Exception as e:
            logger.exception("Error deleting message: receipt_handle=%s, error=%s", receipt_handle, e)
            return False
    
    @transaction.atomic
    def mark_processing(self, job_id: str) -> bool:
        """
        작업을 RUNNING 상태로 변경 (멱등성 보장)
        
        Args:
            job_id: Job ID
            
        Returns:
            bool: 성공 여부
        """
        job = AIJobModel.objects.select_for_update().filter(job_id=job_id).first()
        if not job:
            return False
        
        # 이미 RUNNING이면 OK
        if job.status == "RUNNING":
            return True
        
        # PENDING 상태만 RUNNING으로 변경 가능
        if job.status != "PENDING":
            logger.warning(
                "Cannot mark AI job %s as RUNNING: status=%s",
                job_id,
                job.status,
            )
            return False
        
        job.status = "RUNNING"
        job.locked_at = timezone.now()
        job.locked_by = "sqs-worker"
        
        update_fields = ["status", "locked_at", "locked_by"]
        job.save(update_fields=update_fields)
        return True
    
    @transaction.atomic
    def complete_job(
        self,
        job_id: str,
        result_payload: dict,
    ) -> tuple[bool, str]:
        """
        작업 완료 처리
        
        Args:
            job_id: Job ID
            result_payload: 결과 페이로드
            
        Returns:
            tuple[bool, str]: (성공 여부, 이유)
        """
        job = AIJobModel.objects.select_for_update().filter(job_id=job_id).first()
        if not job:
            return False, "not_found"
        
        # 멱등성: 이미 DONE 상태면 OK
        if job.status == "DONE":
            return True, "idempotent"
        
        job.status = "DONE"
        job.locked_at = None
        job.locked_by = None
        
        # 결과 저장
        from apps.domains.ai.models import AIResultModel
        result, _ = AIResultModel.objects.get_or_create(
            job=job,
            defaults={"payload": result_payload},
        )
        if result.payload != result_payload:
            result.payload = result_payload
            result.save(update_fields=["payload"])
        
        update_fields = ["status", "locked_at", "locked_by"]
        job.save(update_fields=update_fields)
        return True, "ok"
    
    @transaction.atomic
    def fail_job(
        self,
        job_id: str,
        error_message: str,
    ) -> tuple[bool, str]:
        """
        작업 실패 처리
        
        Args:
            job_id: Job ID
            error_message: 에러 메시지
            
        Returns:
            tuple[bool, str]: (성공 여부, 이유)
        """
        job = AIJobModel.objects.select_for_update().filter(job_id=job_id).first()
        if not job:
            return False, "not_found"
        
        # 멱등성: 이미 FAILED 상태면 OK
        if job.status == "FAILED":
            return True, "idempotent"
        
        job.status = "FAILED"
        job.error_message = str(error_message)[:2000]
        job.last_error = str(error_message)[:2000]
        job.locked_at = None
        job.locked_by = None
        
        update_fields = ["status", "error_message", "last_error", "locked_at", "locked_by"]
        job.save(update_fields=update_fields)
        return True, "ok"
