"""
Repository 포트 — 영속화 추상화 (Django/ORM 미사용)
"""
from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import Optional, Protocol

from academy.domain.ai.entities import AIJob, AIJobStatus


class AIJobRepository(Protocol):
    """AI Job 영속화. select_for_update/atomic은 어댑터에서 수행."""

    @abstractmethod
    def get_by_job_id(self, job_id: str) -> Optional[AIJob]:
        """job_id로 조회 (락 없음). 없으면 None."""
        ...

    @abstractmethod
    def get_for_update(self, job_id: str) -> Optional[AIJob]:
        """job_id로 조회 + row lock. 없으면 None."""
        ...

    @abstractmethod
    def save(self, job: AIJob) -> None:
        """엔티티 저장 (insert/update)."""
        ...

    @abstractmethod
    def mark_running(
        self,
        job_id: str,
        worker_id: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool:
        """
        PENDING → RUNNING 전이 (멱등: 이미 RUNNING이면 True).
        Returns: 성공 여부.
        """
        ...

    @abstractmethod
    def mark_done(self, job_id: str, now: datetime, result_payload: Optional[dict] = None) -> bool:
        """RUNNING → DONE. result_payload 있으면 결과 저장. 이미 DONE이면 True (멱등)."""
        ...

    @abstractmethod
    def mark_failed(
        self,
        job_id: str,
        error_message: str,
        tier: str,
        now: datetime,
    ) -> bool:
        """RUNNING → 최종 상태 (tier에 따라 DONE/FAILED 등). 이미 최종 상태면 True (멱등)."""
        ...
