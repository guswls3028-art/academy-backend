"""
Progress Port (인터페이스)

Write-Behind: 진행률 업데이트는 Redis에만 먼저 기록.
DB 부하 감소, 매 작업마다 DB를 치지 않음.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class IProgress(ABC):
    """진행률 저장 추상 인터페이스 (Redis 우선)"""

    @abstractmethod
    def record_progress(
        self,
        job_id: str,
        step: str,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """진행 단계 기록 (Redis에만, TTL 적용)"""
        pass

    @abstractmethod
    def get_progress(self, job_id: str) -> Optional[dict[str, Any]]:
        """진행 상태 조회 (선택)"""
        pass
