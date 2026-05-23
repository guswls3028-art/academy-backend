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
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

from django.utils import timezone

logger = logging.getLogger(__name__)

HEARTBEAT_RETENTION_HOURS = 24
HEARTBEAT_PRUNE_INTERVAL_SECONDS = 3600


def _fetch_instance_id_from_imds(timeout: float = 0.2) -> str:
    """Return EC2 instance-id through IMDSv2 when available."""
    if os.getenv("AWS_EC2_METADATA_DISABLED", "").lower() in {"1", "true", "yes"}:
        return ""

    base_url = "http://169.254.169.254/latest"
    token = ""
    try:
        req = urllib.request.Request(
            f"{base_url}/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            token = resp.read().decode("utf-8").strip()
    except (urllib.error.URLError, TimeoutError, OSError):
        token = ""

    headers = {"X-aws-ec2-metadata-token": token} if token else {}
    try:
        req = urllib.request.Request(f"{base_url}/meta-data/instance-id", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8").strip()[:64]
    except (urllib.error.URLError, TimeoutError, OSError):
        return ""


def _instance_id() -> str:
    """AWS instance-id (메타데이터 fetch 실패 시 hostname fallback). 호출 1회 결과 캐시."""
    cached = getattr(_instance_id, "_cached", None)
    if cached is not None:
        return cached
    iid = (os.getenv("AWS_INSTANCE_ID") or os.getenv("EC2_INSTANCE_ID") or "").strip()
    if not iid:
        iid = _fetch_instance_id_from_imds()
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


def _prune_stale_heartbeats(model, now: datetime) -> None:
    """Keep worker heartbeat telemetry bounded without touching recent alerts."""
    last_pruned_at = getattr(_prune_stale_heartbeats, "_last_pruned_at", None)
    if last_pruned_at is not None and (now - last_pruned_at).total_seconds() < HEARTBEAT_PRUNE_INTERVAL_SECONDS:
        return

    _prune_stale_heartbeats._last_pruned_at = now  # type: ignore[attr-defined]
    cutoff = now - timedelta(hours=HEARTBEAT_RETENTION_HOURS)
    model.objects.filter(last_seen_at__lt=cutoff).delete()


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
        _prune_stale_heartbeats(WorkerHeartbeatModel, ts)
    except Exception:
        logger.warning("worker heartbeat update failed for %s", name, exc_info=False)
