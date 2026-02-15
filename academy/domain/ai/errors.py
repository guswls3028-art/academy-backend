"""
AI 도메인 오류 — 순수 파이썬
"""
from __future__ import annotations


class AIDomainError(Exception):
    """AI 도메인 규칙 위반 등."""
    pass


class JobNotFoundError(AIDomainError):
    """Job이 DB에 없음."""
    pass


class JobNotRunnableError(AIDomainError):
    """상태가 PENDING/RETRYING이 아님."""
    pass
