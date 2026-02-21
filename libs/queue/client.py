"""
Queue 클라이언트 추상화

프로덕션: AWS SQS만 사용 (Redis 제거됨)
"""

import os
import json
import logging
import time
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# 로컬에서 AWS 자격 증명 없을 때 로그 스팸 방지: 인증 오류는 한 번만 로그
_last_auth_error_log = 0.0
_AUTH_ERROR_LOG_INTERVAL = 60.0  # 초


class QueueUnavailableError(Exception):
    """SQS 접근 불가 (자격 증명 없음/만료 등). 로컬에서 흔함. 워커는 이걸 잡고 백오프 후 재시도."""

    def __init__(self, message: str, cause: Optional[Exception] = None):
        self.cause = cause
        super().__init__(message)


def _is_auth_error(e: Exception) -> bool:
    try:
        from botocore.exceptions import ClientError
        if isinstance(e, ClientError):
            code = (e.response or {}).get("Error", {}).get("Code", "")
            return code in (
                "InvalidClientTokenId",
                "UnrecognizedClientException",
                "SignatureDoesNotMatch",
                "InvalidSignatureException",
            )
    except ImportError:
        pass
    return False


def _log_auth_error_once(queue_name: str, op: str, e: Exception) -> None:
    global _last_auth_error_log
    now = time.time()
    if now - _last_auth_error_log >= _AUTH_ERROR_LOG_INTERVAL:
        logger.warning(
            "SQS %s (%s): %s — 로컬에서는 AWS 자격 증명이 없을 수 있음. 60초마다 재시도합니다.",
            op,
            queue_name,
            e,
        )
        _last_auth_error_log = now


class QueueClient(ABC):
    """Queue 클라이언트 추상 인터페이스"""
    
    @abstractmethod
    def send_message(self, queue_name: str, message: Dict[str, Any], delay_seconds: int = 0) -> bool:
        """메시지 전송"""
        pass
    
    @abstractmethod
    def receive_message(self, queue_name: str, wait_time_seconds: int = 20) -> Optional[Dict[str, Any]]:
        """메시지 수신"""
        pass
    
    @abstractmethod
    def delete_message(self, queue_name: str, receipt_handle: str) -> bool:
        """메시지 삭제"""
        pass

    def change_message_visibility(self, queue_name: str, receipt_handle: str, visibility_timeout: int) -> bool:
        """메시지 visibility 연장 (Long job 시 재노출 방지). 기본 구현은 no-op."""
        return True


class SQSQueueClient(QueueClient):
    """AWS SQS 기반 큐 클라이언트 (프로덕션용)"""
    
    def __init__(self, region_name: Optional[str] = None):
        try:
            import boto3
            self.region_name = region_name or os.getenv("AWS_REGION", "ap-northeast-2")
            self.sqs = boto3.client("sqs", region_name=self.region_name)
            logger.info(f"SQSQueueClient initialized: {self.region_name}")
        except ImportError:
            raise ImportError("boto3 package is required for SQSQueueClient")
    
    def _get_queue_url(self, queue_name: str) -> str:
        """큐 이름으로 URL 조회"""
        try:
            response = self.sqs.get_queue_url(QueueName=queue_name)
            return response["QueueUrl"]
        except Exception as e:
            if _is_auth_error(e):
                _log_auth_error_once(queue_name, "get_queue_url", e)
                raise QueueUnavailableError(f"Queue URL unavailable: {e}", cause=e) from e
            logger.error(f"Failed to get queue URL for {queue_name}: {e}")
            raise
    
    def send_message(self, queue_name: str, message: Dict[str, Any], delay_seconds: int = 0) -> bool:
        """SQS에 메시지 전송"""
        try:
            queue_url = self._get_queue_url(queue_name)
            tenant_id = message.get("tenant_id") if isinstance(message, dict) else None
            logger.info(
                "SQS_QUEUE_URL_TRACE | send_message | queue_name=%s queue_url=%s region=%s tenant_id=%s",
                queue_name,
                queue_url,
                self.region_name,
                tenant_id,
            )
            response = self.sqs.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(message),
                DelaySeconds=delay_seconds,
            )
            logger.debug(f"Message sent to {queue_name}: {response['MessageId']}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message to {queue_name}: {e}")
            return False
    
    def receive_message(self, queue_name: str, wait_time_seconds: int = 20) -> Optional[Dict[str, Any]]:
        """SQS에서 메시지 수신"""
        try:
            queue_url = self._get_queue_url(queue_name)
            response = self.sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=wait_time_seconds,
                MessageAttributeNames=["All"],
            )
            messages = response.get("Messages", [])
            if messages:
                return messages[0]
            return None
        except QueueUnavailableError:
            raise
        except Exception as e:
            if _is_auth_error(e):
                _log_auth_error_once(queue_name, "receive_message", e)
                raise QueueUnavailableError(f"Receive unavailable: {e}", cause=e) from e
            logger.error(f"Failed to receive message from {queue_name}: {e}")
            return None
    
    def delete_message(self, queue_name: str, receipt_handle: str) -> bool:
        """SQS 메시지 삭제"""
        try:
            queue_url = self._get_queue_url(queue_name)
            self.sqs.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")
            return False

    def change_message_visibility(
        self, queue_name: str, receipt_handle: str, visibility_timeout: int
    ) -> bool:
        """SQS 메시지 visibility 연장 (장시간 인코딩 시 재노출 방지)."""
        try:
            queue_url = self._get_queue_url(queue_name)
            self.sqs.change_message_visibility(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=visibility_timeout,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to change message visibility: {e}")
            return False


def get_queue_client() -> QueueClient:
    """
    SQS 큐 클라이언트 반환 (Redis 제거됨)
    """
    return SQSQueueClient()
