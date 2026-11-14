"""
Video OpsEvent emission and CloudWatch metrics.

Emit: JOB_DEAD, BATCH_DESYNC, UPLOAD_INTEGRITY_FAIL, ORPHAN_CANCELLED, TENANT_LIMIT_EXCEEDED.
Publish CloudWatch: ActiveJobs, FailedJobs, DeadJobs, UploadFailures.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def emit_ops_event(
    event_type: str,
    *,
    severity: str = "WARNING",
    tenant_id: Optional[int] = None,
    video_id: Optional[int] = None,
    job_id: Optional[str] = None,
    aws_batch_job_id: str = "",
    payload: Optional[dict] = None,
) -> None:
    """Create VideoOpsEvent and optionally publish CloudWatch metric."""
    from apps.domains.video.models import VideoOpsEvent

    try:
        VideoOpsEvent.objects.create(
            type=event_type,
            severity=severity,
            tenant_id=tenant_id,
            video_id=video_id,
            job_id=job_id,
            aws_batch_job_id=(aws_batch_job_id or "")[:256],
            payload=payload or {},
        )
    except Exception as e:
        logger.warning("emit_ops_event failed: %s", e)

    _publish_metric(event_type, tenant_id=tenant_id, video_id=video_id, job_id=job_id, aws_batch_job_id=aws_batch_job_id)


def _publish_metric(
    event_type: str,
    *,
    tenant_id: Optional[int] = None,
    video_id: Optional[int] = None,
    job_id: Optional[str] = None,
    aws_batch_job_id: str = "",
) -> None:
    """Publish CloudWatch metric for event type (DeadJobs, UploadFailures, etc.)."""
    from academy.adapters.monitoring.cloudwatch_metrics import put_video_metric_data

    metric_map = {
        "JOB_DEAD": "DeadJobs",
        "BATCH_DESYNC": "FailedJobs",
        "UPLOAD_INTEGRITY_FAIL": "UploadFailures",
        "ORPHAN_CANCELLED": "DeadJobs",
        "TENANT_LIMIT_EXCEEDED": "ActiveJobs",
    }
    metric_name = metric_map.get(event_type)
    if not metric_name:
        return
    put_video_metric_data([
        {
            "MetricName": metric_name,
            "Value": 1,
            "Unit": "Count",
            "Dimensions": [
                {"Name": "EventType", "Value": event_type},
            ],
        }
    ])


def emit_progress_layer_metrics(
    *,
    progress_requests: int = 0,
    redis_miss: int = 0,
    db_hit: int = 0,
) -> None:
    """Log progress counters without a network call in the HTTP request."""
    if progress_requests == 0 and redis_miss == 0:
        return
    logger.info(
        "video encoding progress poll",
        extra={
            "event": "video_encoding_progress_poll",
            "progress_requests": progress_requests,
            "redis_miss": redis_miss,
            "db_hit": db_hit,
        },
    )
