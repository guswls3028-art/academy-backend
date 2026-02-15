"""
DEPRECATED: ORM 의존성 제거됨 (Gate 7).

AI Job 영속화는 academy adapters를 사용하세요:
  from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
  from academy.adapters.db.django.uow import DjangoUnitOfWork
"""
from __future__ import annotations

def __getattr__(name: str):
    raise ImportError(
        "src.infrastructure.db.ai_repository is deprecated. "
        "Use academy.adapters.db.django.repositories_ai.DjangoAIJobRepository and DjangoUnitOfWork instead."
    )
