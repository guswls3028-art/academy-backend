"""
SQS 큐 깊이 → CloudWatch 메트릭 퍼블리시.

- EventBridge rate(1 minute)로 호출.
- AI/Messaging: Target Tracking (QueueDepth, Academy/Workers)
- Video: B1 TargetTracking (BacklogCount, Academy/VideoProcessing)
  - set_desired_capacity 호출 금지. ASG TargetTrackingPolicy가 스케일 제어.
  - BacklogCount = UPLOADED + PROCESSING (Django DB SSOT)
  - VIDEO_BACKLOG_API_URL 설정 시 해당 API 호출하여 DB 기반 backlog 사용
  - 미설정 시 SQS visible+inflight를 fallback으로 사용 (DB가 SSOT이나 비상용)

설계: docs/B1_METRIC_SCHEMA_EXTRACTION_REPORT.md
"""
from __future__ import annotations

import json
import os
import logging
import urllib.request
from typing import Any

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
AI_QUEUE_LITE = os.environ.get("AI_QUEUE_LITE", "academy-ai-jobs-lite")
AI_QUEUE_BASIC = os.environ.get("AI_QUEUE_BASIC", "academy-ai-jobs-basic")
VIDEO_QUEUE = os.environ.get("VIDEO_QUEUE", "academy-video-jobs")
MESSAGING_QUEUE = os.environ.get("MESSAGING_QUEUE", "academy-messaging-jobs")
NAMESPACE = os.environ.get("METRIC_NAMESPACE", "Academy/Workers")
METRIC_NAME = os.environ.get("METRIC_NAME", "QueueDepth")
AI_WORKER_ASG_NAME = os.environ.get("AI_WORKER_ASG_NAME", "academy-ai-worker-asg")
AI_WORKER_ASG_MAX = int(os.environ.get("AI_WORKER_ASG_MAX", "20"))
VIDEO_WORKER_ASG_NAME = os.environ.get("VIDEO_WORKER_ASG_NAME", "academy-video-worker-asg")
VIDEO_WORKER_ASG_MAX = int(os.environ.get("VIDEO_WORKER_ASG_MAX", "20"))
# VIDEO_BACKLOG_API_INTERNAL: VPC 내부용 base URL (예: http://172.30.x.x:8000). 설정 시 PUBLIC보다 우선.
VIDEO_BACKLOG_API_INTERNAL = os.environ.get("VIDEO_BACKLOG_API_INTERNAL", "").rstrip("/")
VIDEO_BACKLOG_API_URL = os.environ.get("VIDEO_BACKLOG_API_URL", "").rstrip("/")
# 실제 API 호출에 쓸 base: INTERNAL 우선, 없으면 PUBLIC
VIDEO_BACKLOG_API_BASE = VIDEO_BACKLOG_API_INTERNAL or VIDEO_BACKLOG_API_URL
LAMBDA_INTERNAL_API_KEY = os.environ.get("LAMBDA_INTERNAL_API_KEY", "")
MESSAGING_WORKER_ASG_NAME = os.environ.get("MESSAGING_WORKER_ASG_NAME", "academy-messaging-worker-asg")
MESSAGING_WORKER_ASG_MAX = int(os.environ.get("MESSAGING_WORKER_ASG_MAX", "20"))
MESSAGING_WORKER_ASG_MIN = int(os.environ.get("MESSAGING_WORKER_ASG_MIN", "1"))
TARGET_MESSAGES_PER_INSTANCE = int(os.environ.get("TARGET_MESSAGES_PER_INSTANCE", "20"))

BOTO_CONFIG = Config(retries={"max_attempts": 3, "mode": "standard"})

# WAF 등에서 Lambda 기본 User-Agent 차단 방지
HTTP_USER_AGENT = os.environ.get(
    "HTTP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)


def get_queue_counts(sqs_client, queue_name: str) -> tuple[int, int]:
    """(visible, inflight) = ApproximateNumberOfMessagesVisible, ApproximateNumberOfMessagesNotVisible."""
    try:
        url = sqs_client.get_queue_url(QueueName=queue_name)["QueueUrl"]
        attrs = sqs_client.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=[
                "ApproximateNumberOfMessages",  # visible
                "ApproximateNumberOfMessagesNotVisible",  # inflight
            ],
        )
        a = attrs.get("Attributes", {})
        visible = int(a.get("ApproximateNumberOfMessages", 0))
        in_flight = int(a.get("ApproximateNumberOfMessagesNotVisible", 0))
        return visible, in_flight
    except Exception as e:
        logger.warning("SQS get_queue_attributes failed for %s: %s", queue_name, e)
        return 0, 0


def get_visible_count(sqs_client, queue_name: str) -> int:
    visible, _ = get_queue_counts(sqs_client, queue_name)
    return visible


def _fetch_video_backlog_from_api() -> int | None:
    """Django API에서 BacklogCount (QUEUED+RETRY_WAIT) 조회. 실패 시 None. VIDEO_BACKLOG_API_INTERNAL 우선."""
    if not VIDEO_BACKLOG_API_BASE:
        return None
    url = f"{VIDEO_BACKLOG_API_BASE}/api/v1/internal/video/backlog-count/"
    headers = {"User-Agent": HTTP_USER_AGENT}
    if LAMBDA_INTERNAL_API_KEY:
        headers["X-Internal-Key"] = LAMBDA_INTERNAL_API_KEY
    try:
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return int(data.get("backlog", 0))
    except Exception as e:
        logger.warning("VIDEO_BACKLOG_API fetch failed %s: %s", url, e)
        return None


def _is_asg_interrupt_from_api() -> bool:
    """Worker가 Spot/scale-in drain 중 Redis에 설정한 interrupt 플래그. True 시 BacklogCount 퍼블리시 스킵. VIDEO_BACKLOG_API_INTERNAL 우선."""
    if not VIDEO_BACKLOG_API_BASE:
        return False
    url = f"{VIDEO_BACKLOG_API_BASE}/api/v1/internal/video/asg-interrupt-status/"
    headers = {"User-Agent": HTTP_USER_AGENT}
    if LAMBDA_INTERNAL_API_KEY:
        headers["X-Internal-Key"] = LAMBDA_INTERNAL_API_KEY
    try:
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return bool(data.get("interrupt", False))
    except Exception as e:
        logger.debug("asg-interrupt-status fetch failed: %s", e)
        return False


def lambda_handler(event: dict, context: Any) -> dict:
    if _is_asg_interrupt_from_api():
        logger.info("METRIC_PUBLISH_SKIPPED_DURING_INTERRUPT | BacklogCount skip (scale-out runaway 방지)")
        return {"skipped": "asg_interrupt"}

    sqs = boto3.client("sqs", region_name=REGION, config=BOTO_CONFIG)
    cw = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)

    (ai_lite_v, ai_lite_f) = get_queue_counts(sqs, AI_QUEUE_LITE)
    (ai_basic_v, ai_basic_f) = get_queue_counts(sqs, AI_QUEUE_BASIC)
    ai_visible = ai_lite_v + ai_basic_v
    ai_in_flight = ai_lite_f + ai_basic_f
    (video_visible, video_in_flight) = get_queue_counts(sqs, VIDEO_QUEUE)
    (messaging_visible, messaging_in_flight) = get_queue_counts(sqs, MESSAGING_QUEUE)
    ai_total = ai_visible  # 메트릭은 visible만 (기존과 동일)

    # B1: TargetTracking metric = BacklogCount (QUEUED+RETRY_WAIT). API 실패 시 fallback 퍼블리시 안 함 (ASG oscillation 방지).
    video_backlog = _fetch_video_backlog_from_api()

    now = __import__("datetime").datetime.utcnow()
    metric_data = [
        {
            "MetricName": METRIC_NAME,
            "Dimensions": [{"Name": "WorkerType", "Value": "AI"}],
            "Value": float(ai_total),
            "Timestamp": now,
            "Unit": "Count",
        },
        {
            "MetricName": METRIC_NAME,
            "Dimensions": [{"Name": "WorkerType", "Value": "Video"}],
            "Value": float(video_visible),
            "Timestamp": now,
            "Unit": "Count",
        },
        {
            "MetricName": METRIC_NAME,
            "Dimensions": [{"Name": "WorkerType", "Value": "Messaging"}],
            "Value": float(messaging_visible),
            "Timestamp": now,
            "Unit": "Count",
        },
    ]
    cw.put_metric_data(Namespace=NAMESPACE, MetricData=metric_data)

    if video_backlog is not None:
        cw.put_metric_data(
            Namespace="Academy/VideoProcessing",
            MetricData=[
                {
                    "MetricName": "BacklogCount",
                    "Dimensions": [
                        {"Name": "WorkerType", "Value": "Video"},
                        {"Name": "AutoScalingGroupName", "Value": "academy-video-worker-asg"},
                    ],
                    "Value": float(video_backlog),
                    "Timestamp": now,
                    "Unit": "Count",
                }
            ],
        )
        logger.info("BacklogCount metric published | backlog=%d", video_backlog)
    else:
        logger.warning(
            "BacklogCount metric skipped (VIDEO_BACKLOG_API fetch failed); not publishing fallback to prevent ASG oscillation"
        )

    logger.info(
        "queue_depth_metric | ai visible=%d in_flight=%d video visible=%d in_flight=%d backlog=%s messaging visible=%d in_flight=%d",
        ai_visible, ai_in_flight, video_visible, video_in_flight,
        video_backlog if video_backlog is not None else "skipped",
        messaging_visible, messaging_in_flight,
    )
    return {
        "ai_queue_depth": ai_total,
        "video_queue_depth": video_visible,
        "video_backlog_count": video_backlog,
        "messaging_queue_depth": messaging_visible,
    }
