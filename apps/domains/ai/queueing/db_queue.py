# apps/domains/ai/queueing/db_queue.py
"""DB Job Queue — ORM 접근은 academy.adapters.db.django.ai_db_queue_impl 로 위임 (Gate 7)."""
from __future__ import annotations

from academy.adapters.db.django.ai_db_queue_impl import DjangoDBJobQueue, DBQueueConfig

DBJobQueue = DjangoDBJobQueue
__all__ = ["DBJobQueue", "DBQueueConfig"]
