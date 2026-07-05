"""CloudWatch metric publishing adapter."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def put_video_metric_data(metric_data: list[dict[str, Any]]) -> bool:
    if not metric_data:
        return True
    try:
        import boto3
        from django.conf import settings

        region = getattr(settings, "AWS_DEFAULT_REGION", None) or getattr(settings, "AWS_REGION", "ap-northeast-2")
        namespace = getattr(settings, "VIDEO_CLOUDWATCH_NAMESPACE", "Academy/Video")
        cw = boto3.client("cloudwatch", region_name=region)
        cw.put_metric_data(Namespace=namespace, MetricData=metric_data)
        return True
    except Exception as e:
        logger.debug("CloudWatch put_metric_data failed: %s", e)
        return False
