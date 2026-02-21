"""
SQS 큐 깊이 → CloudWatch 메트릭 퍼블리시.

- EventBridge rate(1 minute)로 호출.
- AI/Messaging: Target Tracking (QueueDepth)
- Video: Lambda 단독 컨트롤. set_desired_capacity 직접 호출.
  desired = clamp(min, max, visible + inflight)
  scale-in: visible==0 AND inflight==0 가 STABLE_WINDOW_SECONDS 이상 지속 시에만 min으로 감소.

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
METRIC_BACKLOG_PER_INSTANCE = "BacklogPerInstance"
AI_WORKER_ASG_NAME = os.environ.get("AI_WORKER_ASG_NAME", "academy-ai-worker-asg")
AI_WORKER_ASG_MAX = int(os.environ.get("AI_WORKER_ASG_MAX", "20"))
VIDEO_WORKER_ASG_NAME = os.environ.get("VIDEO_WORKER_ASG_NAME", "academy-video-worker-asg")
VIDEO_WORKER_ASG_MAX = int(os.environ.get("VIDEO_WORKER_ASG_MAX", "20"))
VIDEO_WORKER_ASG_MIN = int(os.environ.get("VIDEO_WORKER_ASG_MIN", "0"))
MESSAGING_WORKER_ASG_NAME = os.environ.get("MESSAGING_WORKER_ASG_NAME", "academy-messaging-worker-asg")
MESSAGING_WORKER_ASG_MAX = int(os.environ.get("MESSAGING_WORKER_ASG_MAX", "20"))
MESSAGING_WORKER_ASG_MIN = int(os.environ.get("MESSAGING_WORKER_ASG_MIN", "1"))
TARGET_MESSAGES_PER_INSTANCE = int(os.environ.get("TARGET_MESSAGES_PER_INSTANCE", "20"))

BOTO_CONFIG = Config(retries={"max_attempts": 3, "mode": "standard"})


def get_queue_counts(sqs_client, queue_name: str) -> tuple[int, int]:
    """(visible, in_flight) = ApproximateNumberOfMessages, ApproximateNumberOfMessagesNotVisible."""
    try:
        url = sqs_client.get_queue_url(QueueName=queue_name)["QueueUrl"]
        attrs = sqs_client.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
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


def get_in_service_count(autoscaling_client, asg_name: str) -> int:
    """ASG InService 인스턴스 수."""
    try:
        asgs = autoscaling_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name],
        )
        if not asgs.get("AutoScalingGroups"):
            return 0
        instances = asgs["AutoScalingGroups"][0].get("Instances", [])
        return sum(1 for i in instances if i.get("LifecycleState") == "InService")
    except Exception as e:
        logger.warning("get_in_service_count failed for %s: %s", asg_name, e)
        return 1  # fallback: 1로 나눠서 metric 푸시


def lambda_handler(event: dict, context: Any) -> dict:
    sqs = boto3.client("sqs", region_name=REGION, config=BOTO_CONFIG)
    cw = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)
    autoscaling = boto3.client("autoscaling", region_name=REGION, config=BOTO_CONFIG)

    (ai_lite_v, ai_lite_f) = get_queue_counts(sqs, AI_QUEUE_LITE)
    (ai_basic_v, ai_basic_f) = get_queue_counts(sqs, AI_QUEUE_BASIC)
    ai_visible = ai_lite_v + ai_basic_v
    ai_in_flight = ai_lite_f + ai_basic_f
    (video_visible, video_in_flight) = get_queue_counts(sqs, VIDEO_QUEUE)
    (messaging_visible, messaging_in_flight) = get_queue_counts(sqs, MESSAGING_QUEUE)
    ai_total = ai_visible  # 메트릭은 visible만 (기존과 동일)

    # Video: BacklogPerInstance = VisibleMessages / max(1, InServiceInstances)
    # 락 대기/재시도 메시지 과도 scale-out 방지. Target Tracking이 이 메트릭만 사용.
    video_in_service = get_in_service_count(autoscaling, VIDEO_WORKER_ASG_NAME)
    video_backlog_per_instance = float(video_visible) / max(1, video_in_service)

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
        {
            "MetricName": METRIC_BACKLOG_PER_INSTANCE,
            "Dimensions": [{"Name": "WorkerType", "Value": "Video"}],
            "Value": video_backlog_per_instance,
            "Timestamp": now,
            "Unit": "None",
        },
    ]
    cw.put_metric_data(Namespace=NAMESPACE, MetricData=metric_data)

    logger.info(
        "queue_depth_metric | ai visible=%d in_flight=%d video visible=%d in_flight=%d in_service=%d backlog_per_instance=%.2f messaging visible=%d in_flight=%d",
        ai_visible, ai_in_flight, video_visible, video_in_flight, video_in_service, video_backlog_per_instance, messaging_visible, messaging_in_flight,
    )
    return {
        "ai_queue_depth": ai_total,
        "video_queue_depth": video_visible,
        "video_backlog_per_instance": video_backlog_per_instance,
        "messaging_queue_depth": messaging_visible,
    }
