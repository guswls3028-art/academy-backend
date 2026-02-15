"""
AI SQS Queue 어댑터 — receive/delete/extend_visibility (기존 AISQSQueue 래핑)
"""
from __future__ import annotations

from typing import Any, Optional


class SQSAIQueueAdapter:
    """AIQueuePort 구현. apps.support.ai.services.sqs_queue.AISQSQueue 래핑 + extend_visibility 노출."""

    def __init__(self) -> None:
        # Lazy: Django 설정 로드 후 import
        self._impl = None

    def _get_impl(self):
        if self._impl is None:
            from apps.support.ai.services.sqs_queue import AISQSQueue
            self._impl = AISQSQueue()
        return self._impl

    def receive(self, tier: str, wait_time_seconds: int = 20) -> Optional[dict[str, Any]]:
        return self._get_impl().receive_message(tier=tier, wait_time_seconds=wait_time_seconds)

    def delete(self, receipt_handle: str, tier: str) -> bool:
        return self._get_impl().delete_message(receipt_handle=receipt_handle, tier=tier)

    def extend_visibility(
        self, receipt_handle: str, tier: str, visibility_timeout_seconds: int
    ) -> bool:
        return self._get_impl().change_message_visibility(
            receipt_handle=receipt_handle,
            tier=tier,
            visibility_timeout=visibility_timeout_seconds,
        )
