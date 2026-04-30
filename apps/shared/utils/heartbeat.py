"""Worker heartbeat 헬퍼 — SQS polling 루프에서 호출.

사용:
    from apps.shared.utils.heartbeat import beat
    beat("ai_cpu")  # 매 polling cycle 1회

stale 임계: check_dev_alerts.rule_stale_workers (default 5분).
실패 시 silent skip — heartbeat가 polling을 절대 차단하지 않게.
"""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime
from typing import Optional

from django.utils import timezone

logger = logging.getLogger(__name__)


def _instance_id() -> str:
    """AWS instance-id (메타데이터 fetch 실패 시 hostname fallback). 호출 1회 결과 캐시."""
    cached = getattr(_instance_id, "_cached", None)
    if cached is not None:
        return cached
    iid = os.getenv("AWS_INSTANCE_ID", "").strip()
    if not iid:
        try:
            iid = socket.gethostname()[:64]
        except Exception:
            iid = "unknown"
    _instance_id._cached = iid  # type: ignore[attr-defined]
    return iid


def _running_version() -> str:
    """배포된 이미지 sha. CI build이 컨테이너에 주입한 ENV 우선."""
    return (os.getenv("ACADEMY_GIT_SHA") or os.getenv("GIT_SHA") or "")[:64]


def beat(name: str, *, now: Optional[datetime] = None) -> None:
    """Heartbeat 1회 갱신. 실패는 silent (워커 polling 차단 금지)."""
    try:
        from apps.core.models import WorkerHeartbeatModel
    except Exception:
        # Django app 미초기화 등 예외 케이스 — 정상 운영 외 환경.
        return

    try:
        ts = now or timezone.now()
        WorkerHeartbeatModel.objects.update_or_create(
            name=name,
            instance=_instance_id(),
            defaults={
                "last_seen_at": ts,
                "version": _running_version(),
            },
        )
    except Exception:
        logger.warning("worker heartbeat update failed for %s", name, exc_info=False)
