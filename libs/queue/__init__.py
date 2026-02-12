"""
Queue 추상화 레이어

AWS SQS만 사용 (Redis 제거됨)

사용 예:
    from libs.queue import get_queue_client
    
    queue = get_queue_client()
    queue.send_message(queue_name="ai-jobs", message={"job_id": "123"})
    message = queue.receive_message(queue_name="ai-jobs")
"""

from .client import QueueClient, get_queue_client

__all__ = ["QueueClient", "get_queue_client"]
