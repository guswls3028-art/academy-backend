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
VIDEO_BACKLOG_API_URL = os.environ.get("VIDEO_BACKLOG_API_URL", "").rstrip("/")
LAMBDA_INTERNAL_API_KEY = os.environ.get("LAMBDA_INTERNAL_API_KEY", "")
MESSAGING_WORKER_ASG_NAME = os.environ.get("MESSAGING_WORKER_ASG_NAME", "academy-messaging-worker-asg")
MESSAGING_WORKER_ASG_MAX = int(os.environ.get("MESSAGING_WORKER_ASG_MAX", "20"))
MESSAGING_WORKER_ASG_MIN = int(os.environ.get("MESSAGING_WORKER_ASG_MIN", "1"))
TARGET_MESSAGES_PER_INSTANCE = int(os.environ.get("TARGET_MESSAGES_PER_INSTANCE", "20"))

BOTO_CONFIG = Config(retries={"max_attempts": 3, "mode": "standard"})


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
    """Django API에서 BacklogCount (QUEUED+RETRY_WAIT) 조회. 실패 시 None."""
    if not VIDEO_BACKLOG_API_URL:
        return None
    url = f"{VIDEO_BACKLOG_API_URL}/api/v1/internal/video/backlog-count/"
    headers = {}
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


def _fetch_video_backlog_score_from_api() -> float | None:
    """Django API에서 BacklogScore (QUEUED*1 + RETRY_WAIT*2) 조회. 실패 시 None."""
    if not VIDEO_BACKLOG_API_URL:
        return None
    url = f"{VIDEO_BACKLOG_API_URL}/api/v1/internal/video/backlog-score/"
    headers = {}
    if LAMBDA_INTERNAL_API_KEY:
        headers["X-Internal-Key"] = LAMBDA_INTERNAL_API_KEY
    try:
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return float(data.get("backlog_score", 0))
    except Exception as e:
        logger.warning("VIDEO_BACKLOG_SCORE_API fetch failed %s: %s", url, e)
        return None


def lambda_handler(event: dict, context: Any) -> dict:
    sqs = boto3.client("sqs", region_name=REGION, config=BOTO_CONFIG)
    cw = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)

    (ai_lite_v, ai_lite_f) = get_queue_counts(sqs, AI_QUEUE_LITE)
    (ai_basic_v, ai_basic_f) = get_queue_counts(sqs, AI_QUEUE_BASIC)
    ai_visible = ai_lite_v + ai_basic_v
    ai_in_flight = ai_lite_f + ai_basic_f
    (video_visible, video_in_flight) = get_queue_counts(sqs, VIDEO_QUEUE)
    (messaging_visible, messaging_in_flight) = get_queue_counts(sqs, MESSAGING_QUEUE)
    ai_total = ai_visible  # 메트릭은 visible만 (기존과 동일)

    # B1: BacklogScore = SUM(QUEUED=>1, RETRY_WAIT=>2). API 없으면 SQS fallback.
    video_backlog_score = _fetch_video_backlog_score_from_api()
    if video_backlog_score is None:
        video_backlog_score = float(video_visible + video_in_flight)  # fallback
        if not VIDEO_BACKLOG_API_URL:
            logger.info("VIDEO_BACKLOG_API_URL not set; using SQS fallback (visible+inflight)=%.0f", video_backlog_score)

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

    logger.info(
        "queue_depth_metric | ai visible=%d in_flight=%d video visible=%d in_flight=%d backlog=%d messaging visible=%d in_flight=%d",
        ai_visible, ai_in_flight, video_visible, video_in_flight, video_backlog,
        messaging_visible, messaging_in_flight,
    )
    return {
        "ai_queue_depth": ai_total,
        "video_queue_depth": video_visible,
        "video_backlog_count": video_backlog,
        "messaging_queue_depth": messaging_visible,
    }
