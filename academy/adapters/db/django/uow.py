"""
Django Unit of Work — transaction.atomic 래퍼 (lazy import)
"""
from __future__ import annotations


class DjangoUnitOfWork:
    """Django transaction.atomic으로 트랜잭션 경계. 메서드 내부에서 Django import."""

    def __init__(self) -> None:
        self._atomic = None
        self._ai_jobs = None

    @property
    def ai_jobs(self):
        from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
        if self._ai_jobs is None:
            self._ai_jobs = DjangoAIJobRepository()
        return self._ai_jobs

    def __enter__(self) -> DjangoUnitOfWork:
        from django.db import transaction
        self._atomic = transaction.atomic()
        self._atomic.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._atomic is not None:
            self._atomic.__exit__(exc_type, exc_val, exc_tb)
            self._atomic = None

    def commit(self) -> None:
        # atomic() 블록 내에서는 명시적 commit 없음; __exit__ 시 자동
        pass

    def rollback(self) -> None:
        from django.db import transaction
        transaction.set_rollback(True)
