"""
Unit of Work 포트 — 트랜잭션 경계 (Django 미사용)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from academy.application.ports.repositories import AIJobRepository


class UnitOfWork(Protocol):
    """트랜잭션 단위. __enter__에서 시작, __exit__에서 commit/rollback."""

    @property
    def ai_jobs(self) -> AIJobRepository:
        ...

    def __enter__(self) -> "UnitOfWork":
        ...

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        ...

    def commit(self) -> None:
        ...

    def rollback(self) -> None:
        ...
