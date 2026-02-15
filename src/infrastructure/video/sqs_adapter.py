"""
Video SQS Adapter - IVideoQueue 구현체

SQS receive/delete만. DB 상태는 VideoRepository가 담당.
"""
from __future__ import annotations

from src.application.ports.video_queue import IVideoQueue
from apps.support.video.services.sqs_queue import VideoSQSQueue as _VideoSQSQueue


class VideoSQSAdapter(IVideoQueue):
    """IVideoQueue 포트 구현 (SQS만, DB 무관)"""

    def __init__(self) -> None:
        self._impl = _VideoSQSQueue()

    def receive_message(self, wait_time_seconds: int = 20):
        return self._impl.receive_message(wait_time_seconds=wait_time_seconds)

    def delete_message(self, receipt_handle: str) -> bool:
        return self._impl.delete_message(receipt_handle=receipt_handle)

    def change_message_visibility(self, receipt_handle: str, visibility_timeout: int = 10800) -> bool:
        """장시간 인코딩 시 재노출 방지 (작업 시작 직후 호출)."""
        return self._impl.change_message_visibility(receipt_handle, visibility_timeout)

    def _get_queue_name(self) -> str:
        """로깅용"""
        return self._impl._get_queue_name()
