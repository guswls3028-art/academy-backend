"""
AI Queue Port (인터페이스)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class IAIQueue(ABC):
    """AI Job Queue 추상 인터페이스"""

    @abstractmethod
    def receive_message(self, tier: str, wait_time_seconds: int = 20) -> Optional[dict]:
        pass

    @abstractmethod
    def delete_message(self, receipt_handle: str, tier: str) -> bool:
        pass

    @abstractmethod
    def mark_processing(self, job_id: str) -> bool:
        pass

    @abstractmethod
    def complete_job(
        self,
        job_id: str,
        result_payload: dict,
    ) -> tuple[bool, str]:
        pass

    @abstractmethod
    def fail_job(self, job_id: str, error_message: str) -> tuple[bool, str]:
        pass
