"""
SQS 큐 깊이 → CloudWatch 메트릭 퍼블리시 (ASG Target Tracking용).

- EventBridge rate(1 minute)로 호출.
- AI: academy-ai-jobs-lite + academy-ai-jobs-basic 합산 → Academy/Workers, WorkerType=AI
- Video: academy-video-jobs → Academy/Workers, WorkerType=Video
- Messaging: academy-messaging-jobs → Academy/Workers, WorkerType=Messaging

설계: docs/SSOT_0215/IMPORTANT/ARCH_CHANGE_PROPOSAL_LAMBDA_TO_ASG.md
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

BOTO_CONFIG = Config(retries={"max_attempts": 3, "mode": "standard"})


def get_visible_count(sqs_client, queue_name: str) -> int:
    try:
        url = sqs_client.get_queue_url(QueueName=queue_name)["QueueUrl"]
        attrs = sqs_client.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        return int(attrs.get("Attributes", {}).get("ApproximateNumberOfMessages", 0))
    except Exception as e:
        logger.warning("SQS get_queue_attributes failed for %s: %s", queue_name, e)
        return 0


def lambda_handler(event: dict, context: Any) -> dict:
    sqs = boto3.client("sqs", region_name=REGION, config=BOTO_CONFIG)
    cw = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)

    ai_lite = get_visible_count(sqs, AI_QUEUE_LITE)
    ai_basic = get_visible_count(sqs, AI_QUEUE_BASIC)
    video_count = get_visible_count(sqs, VIDEO_QUEUE)
    messaging_count = get_visible_count(sqs, MESSAGING_QUEUE)
    ai_total = ai_lite + ai_basic

    now = __import__("datetime").datetime.utcnow()
    cw.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[
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
                "Value": float(video_count),
                "Timestamp": now,
                "Unit": "Count",
            },
            {
                "MetricName": METRIC_NAME,
                "Dimensions": [{"Name": "WorkerType", "Value": "Messaging"}],
                "Value": float(messaging_count),
                "Timestamp": now,
                "Unit": "Count",
            },
        ],
    )
    logger.info(
        "queue_depth_metric | ai=%d (lite=%d+basic=%d) video=%d messaging=%d",
        ai_total, ai_lite, ai_basic, video_count, messaging_count,
    )
    return {
        "ai_queue_depth": ai_total,
        "video_queue_depth": video_count,
        "messaging_queue_depth": messaging_count,
    }
