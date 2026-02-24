"""
Video OpsEvent emission and CloudWatch metrics.

Emit: JOB_DEAD, BATCH_DESYNC, UPLOAD_INTEGRITY_FAIL, ORPHAN_CANCELLED, TENANT_LIMIT_EXCEEDED.
Publish CloudWatch: ActiveJobs, FailedJobs, DeadJobs, UploadFailures.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

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
    from apps.support.video.models import VideoOpsEvent

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
    try:
        import boto3
        from django.conf import settings
        region = getattr(settings, "AWS_DEFAULT_REGION", None) or getattr(settings, "AWS_REGION", "ap-northeast-2")
        namespace = getattr(settings, "VIDEO_CLOUDWATCH_NAMESPACE", "Academy/Video")
        cw = boto3.client("cloudwatch", region_name=region)
        cw.put_metric_data(
            Namespace=namespace,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": 1,
                    "Unit": "Count",
                    "Dimensions": [
                        {"Name": "EventType", "Value": event_type},
                    ],
                }
            ],
        )
    except Exception as e:
        logger.debug("CloudWatch put_metric_data failed: %s", e)


def emit_progress_layer_metrics(
    *,
    progress_requests: int = 0,
    redis_miss: int = 0,
    db_hit: int = 0,
) -> None:
    """Progress endpoint 관측: ProgressRequests, RedisMiss, ProgressEndpointDBHit (0 기대)."""
    if progress_requests == 0 and redis_miss == 0:
        return
    try:
        import boto3
        from django.conf import settings
        region = getattr(settings, "AWS_DEFAULT_REGION", None) or getattr(settings, "AWS_REGION", "ap-northeast-2")
        namespace = getattr(settings, "VIDEO_CLOUDWATCH_NAMESPACE", "Academy/Video")
        cw = boto3.client("cloudwatch", region_name=region)
        metrics = []
        if progress_requests:
            metrics.append({
                "MetricName": "ProgressRequests",
                "Value": progress_requests,
                "Unit": "Count",
            })
        if redis_miss:
            metrics.append({
                "MetricName": "RedisMiss",
                "Value": redis_miss,
                "Unit": "Count",
            })
        metrics.append({
            "MetricName": "ProgressEndpointDBHit",
            "Value": db_hit,
            "Unit": "Count",
        })
        cw.put_metric_data(
            Namespace=namespace,
            MetricData=metrics,
        )
    except Exception as e:
        logger.debug("CloudWatch progress layer metrics failed: %s", e)
