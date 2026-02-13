"""
AI SQS Adapter - IAIQueue 구현체

apps.support.ai.services.sqs_queue.AISQSQueue를 래핑하여
Application 포트(IAIQueue)를 구현.
WORKER_TYPE=CPU/GPU 환경 변수 또는 use_gpu에 따라 tier 결정.
"""
from __future__ import annotations

import os
from typing import Optional

from src.application.ports.ai_queue import IAIQueue
from apps.support.ai.services.sqs_queue import AISQSQueue as _AISQSQueue


def _is_gpu_worker() -> bool:
    """WORKER_TYPE 환경 변수로 CPU/GPU 분기 (GPU: premium, CPU: lite/basic)"""
    t = (os.environ.get("WORKER_TYPE") or os.environ.get("AI_WORKER_MODE") or "").lower()
    return t == "gpu"


class AISQSAdapter(IAIQueue):
    """IAIQueue 포트 구현 (AISQSQueue 위임)"""

    def __init__(self) -> None:
        self._impl = _AISQSQueue()

    def _resolve_tier(self, tier: Optional[str] = None, use_gpu: Optional[bool] = None) -> str:
        """tier > use_gpu > WORKER_TYPE 순으로 tier 결정"""
        if tier is not None:
            return tier
        if use_gpu is not None:
            return "premium" if use_gpu else "basic"
        return "premium" if _is_gpu_worker() else "basic"

    def receive_message(
        self,
        tier: Optional[str] = None,
        wait_time_seconds: int = 20,
        use_gpu: Optional[bool] = None,
    ):
        t = self._resolve_tier(tier=tier, use_gpu=use_gpu)
        return self._impl.receive_message(tier=t, wait_time_seconds=wait_time_seconds)

    def delete_message(
        self,
        receipt_handle: str,
        tier: Optional[str] = None,
        use_gpu: Optional[bool] = None,
    ) -> bool:
        t = self._resolve_tier(tier=tier, use_gpu=use_gpu)
        return self._impl.delete_message(receipt_handle=receipt_handle, tier=t)

    def _get_queue_name(self, tier: Optional[str] = None, use_gpu: Optional[bool] = None) -> str:
        t = self._resolve_tier(tier=tier, use_gpu=use_gpu)
        return self._impl._get_queue_name(tier=t)

    def mark_processing(self, job_id: str) -> bool:
        return self._impl.mark_processing(job_id=job_id)

    def complete_job(
        self,
        job_id: str,
        result_payload: dict,
    ) -> tuple[bool, str]:
        return self._impl.complete_job(
            job_id=job_id,
            result_payload=result_payload,
        )

    def fail_job(self, job_id: str, error_message: str) -> tuple[bool, str]:
        return self._impl.fail_job(
            job_id=job_id,
            error_message=error_message,
        )
