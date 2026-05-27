"""Tools SQS queue adapter for non-AI document conversion jobs."""

from __future__ import annotations

from typing import Any, Optional

from django.conf import settings


class SQSToolsQueueAdapter:
    """Queue adapter with the same shape as SQSAIQueueAdapter."""

    def __init__(self, queue_name: str | None = None) -> None:
        self.queue_name = queue_name or getattr(
            settings,
            "TOOLS_SQS_QUEUE_NAME",
            "academy-v1-tools-queue",
        )
        self._impl = None

    def _get_impl(self):
        if self._impl is None:
            from apps.support.ai.services.sqs_queue import AISQSQueue
            self._impl = AISQSQueue(queue_name_override=self.queue_name)
        return self._impl

    def receive(self, tier: str = "tools", wait_time_seconds: int = 20) -> Optional[dict[str, Any]]:
        return self._get_impl().receive_message(tier=tier, wait_time_seconds=wait_time_seconds)

    def delete(self, receipt_handle: str, tier: str = "tools") -> bool:
        return self._get_impl().delete_message(receipt_handle=receipt_handle, tier=tier)

    def extend_visibility(
        self,
        receipt_handle: str,
        tier: str = "tools",
        visibility_timeout_seconds: int = 3600,
    ) -> bool:
        return self._get_impl().change_message_visibility(
            receipt_handle=receipt_handle,
            tier=tier,
            visibility_timeout=visibility_timeout_seconds,
        )
