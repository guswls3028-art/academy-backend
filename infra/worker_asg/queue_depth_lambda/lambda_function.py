"""
SQS 큐 깊이 → CloudWatch 메트릭 퍼블리시.

- EventBridge rate(1 minute)로 호출.
- AI/Messaging: Target Tracking (QueueDepth, Academy/Workers)
- Video: 스케일링은 **오직 SQS** 기준. DB/backlog API 미사용.
  - Academy/VideoProcessing VideoQueueDepthTotal = SQS(visible + notVisible) 합산.
  - ASG TargetTracking이 이 메트릭만 사용 (Scale Trigger Source = Worker Pull Source = SQS).

설계: docs/VIDEO_WORKER_SCALING_SSOT.md
"""
from __future__ import annotations

import os
import logging
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
MESSAGING_WORKER_ASG_NAME = os.environ.get("MESSAGING_WORKER_ASG_NAME", "academy-messaging-worker-asg")
MESSAGING_WORKER_ASG_MAX = int(os.environ.get("MESSAGING_WORKER_ASG_MAX", "20"))
MESSAGING_WORKER_ASG_MIN = int(os.environ.get("MESSAGING_WORKER_ASG_MIN", "1"))
TARGET_MESSAGES_PER_INSTANCE = int(os.environ.get("TARGET_MESSAGES_PER_INSTANCE", "20"))
# Video 스케일링용 커스텀 메트릭 이름 (SQS total only)
VIDEO_QUEUE_DEPTH_METRIC = os.environ.get("VIDEO_QUEUE_DEPTH_METRIC", "VideoQueueDepthTotal")

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

    # Video 스케일링: 오직 SQS total(visible + notVisible)만 사용. DB/backlog API 미사용.
    video_queue_depth_total = video_visible + video_in_flight

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

    # Academy/VideoProcessing: ASG TargetTracking용. SQS total만 발행.
    cw.put_metric_data(
        Namespace="Academy/VideoProcessing",
        MetricData=[
            {
                "MetricName": VIDEO_QUEUE_DEPTH_METRIC,
                "Dimensions": [
                    {"Name": "WorkerType", "Value": "Video"},
                    {"Name": "AutoScalingGroupName", "Value": VIDEO_WORKER_ASG_NAME},
                ],
                "Value": float(video_queue_depth_total),
                "Timestamp": now,
                "Unit": "Count",
            }
        ],
    )
    logger.info(
        "VideoQueueDepthTotal published | visible=%d notVisible=%d total=%d",
        video_visible, video_in_flight, video_queue_depth_total,
    )

    logger.info(
        "queue_depth_metric | ai visible=%d in_flight=%d video visible=%d in_flight=%d total=%d messaging visible=%d in_flight=%d",
        ai_visible, ai_in_flight, video_visible, video_in_flight, video_queue_depth_total,
        messaging_visible, messaging_in_flight,
    )
    return {
        "ai_queue_depth": ai_total,
        "video_queue_depth": video_visible,
        "video_queue_depth_total": video_queue_depth_total,
        "messaging_queue_depth": messaging_visible,
    }
