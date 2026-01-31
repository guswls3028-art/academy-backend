# apps/domains/ai/queueing/interfaces.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Dict, Any


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    job_type: str
    payload: Dict[str, Any]
    tenant_id: Optional[str] = None
    source_domain: Optional[str] = None
    source_id: Optional[str] = None

    # lease info (debug/ops)
    locked_by: Optional[str] = None


class JobQueue(Protocol):
    def publish(self, *, job_id: str) -> None:
        ...

    def claim(self, *, worker_id: str) -> Optional[ClaimedJob]:
        ...

    def heartbeat(self, *, job_id: str, worker_id: str) -> None:
        ...

    def mark_done(self, *, job_id: str) -> None:
        ...

    def mark_failed(self, *, job_id: str, error: str, retryable: bool = True) -> None:
        ...
