"""
Idempotency Port (인터페이스)
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class IIdempotency(ABC):
    @abstractmethod
    def acquire_lock(self, job_id: str) -> bool:
        pass

    @abstractmethod
    def release_lock(self, job_id: str) -> None:
        pass
