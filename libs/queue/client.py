"""
Queue 클라이언트 추상화

프로덕션: AWS SQS만 사용 (Redis 제거됨)
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


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
            logger.error(f"Failed to get queue URL for {queue_name}: {e}")
            raise
    
    def send_message(self, queue_name: str, message: Dict[str, Any], delay_seconds: int = 0) -> bool:
        """SQS에 메시지 전송"""
        try:
            queue_url = self._get_queue_url(queue_name)
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
        except Exception as e:
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


def get_queue_client() -> QueueClient:
    """
    SQS 큐 클라이언트 반환 (Redis 제거됨)
    """
    return SQSQueueClient()
