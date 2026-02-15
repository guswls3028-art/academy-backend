"""
Queue 포트 — SQS 수신/삭제/visibility 연장 (boto3 미사용)
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Optional, Protocol


class AIQueuePort(Protocol):
    """AI SQS 큐: 수신, 삭제, visibility 연장."""

    @abstractmethod
    def receive(self, tier: str, wait_time_seconds: int = 20) -> Optional[dict[str, Any]]:
        """메시지 1건 수신. keys: job_id, job_type, tier, payload, receipt_handle, ..."""
        ...

    @abstractmethod
    def delete(self, receipt_handle: str, tier: str) -> bool:
        """메시지 삭제."""
        ...

    @abstractmethod
    def extend_visibility(self, receipt_handle: str, tier: str, visibility_timeout_seconds: int) -> bool:
        """장시간 작업 시 visibility 연장 (AI Worker 필수)."""
        ...


class VisibilityExtenderPort(Protocol):
    """장시간 작업 중 주기적으로 visibility 연장하는 역할."""

    def start(self, receipt_handle: str, tier: str, interval_seconds: int, visibility_timeout_seconds: int) -> None:
        """연장 스레드/타이머 시작."""
        ...

    def stop(self) -> None:
        """연장 중지."""
        ...
