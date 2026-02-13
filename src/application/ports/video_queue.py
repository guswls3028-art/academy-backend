"""
Video Queue Port (인터페이스)

SQS 수신/삭제만. DB 상태는 IVideoRepository가 담당.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class IVideoQueue(ABC):
    """Video Job Queue 추상 인터페이스 (SQS receive/delete만)"""

    @abstractmethod
    def receive_message(self, wait_time_seconds: int = 20) -> Optional[dict]:
        pass

    @abstractmethod
    def delete_message(self, receipt_handle: str) -> bool:
        pass
