"""
AI Job Repository Port (인터페이스)

DB 상태 업데이트: mark_processing, complete_job, fail_job
Worker는 이 포트를 통해서만 AI Job 상태를 변경.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class IAIJobRepository(ABC):
    """AI Job 상태 Repository 추상 인터페이스"""

    @abstractmethod
    def mark_processing(self, job_id: str) -> bool:
        """작업을 RUNNING 상태로 변경 (멱등성 보장)"""
        pass

    @abstractmethod
    def complete_job(
        self,
        job_id: str,
        result_payload: dict,
    ) -> tuple[bool, str]:
        """작업 완료 (DONE 상태로 전환)"""
        pass

    @abstractmethod
    def fail_job(self, job_id: str, error_message: str) -> tuple[bool, str]:
        """작업 실패 (FAILED 상태로 전환)"""
        pass
