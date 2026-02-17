"""
SQS 큐 깊이 → CloudWatch 메트릭 퍼블리시 + AI 워커 ASG 원하는 용량 조정.

- EventBridge rate(1 minute)로 호출.
- AI: academy-ai-jobs-lite + academy-ai-jobs-basic 합산 → Academy/Workers, WorkerType=AI
- Video: academy-video-jobs → Academy/Workers, WorkerType=Video
- Messaging: academy-messaging-jobs → Academy/Workers, WorkerType=Messaging

Application Auto Scaling(ec2:autoScalingGroup:DesiredCapacity)이 일부 계정/리전에서
허용되지 않으므로, EC2 Auto Scaling API(set_desired_capacity)로 직접 조정함.
- (visible + in_flight) > 0 → desired >= 1 (처리 중인 메시지 있을 때는 scale to 0 안 함)
- (visible + in_flight) == 0 → desired = 0

설계: docs/SSOT_0215/IMPORTANT/ARCH_CHANGE_PROPOSAL_LAMBDA_TO_ASG.md
"""
from __future__ import annotations

import math
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


def set_ai_worker_asg_desired(autoscaling_client, ai_visible: int, ai_in_flight: int) -> None:
    """보이는 메시지 + 처리 중(in flight) 둘 다 0일 때만 desired=0. 처리 중인데 종료되지 않도록."""
    ai_total_for_scale = ai_visible + ai_in_flight
    if ai_total_for_scale > 0:
        new_desired = min(AI_WORKER_ASG_MAX, max(1, math.ceil(ai_total_for_scale / 20)))
    else:
        new_desired = 0

    try:
        asgs = autoscaling_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[AI_WORKER_ASG_NAME],
        )
        if not asgs.get("AutoScalingGroups"):
            logger.warning("ASG not found: %s", AI_WORKER_ASG_NAME)
            return
        current = asgs["AutoScalingGroups"][0]["DesiredCapacity"]
        if current == new_desired:
            return
        autoscaling_client.set_desired_capacity(
            AutoScalingGroupName=AI_WORKER_ASG_NAME,
            DesiredCapacity=new_desired,
        )
        logger.info(
            "ai_worker_asg desired %s -> %s (visible=%d in_flight=%d)",
            current, new_desired, ai_visible, ai_in_flight,
        )
    except Exception as e:
        logger.warning("set_ai_worker_asg_desired failed: %s", e)


def lambda_handler(event: dict, context: Any) -> dict:
    sqs = boto3.client("sqs", region_name=REGION, config=BOTO_CONFIG)
    cw = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)
    autoscaling = boto3.client("autoscaling", region_name=REGION, config=BOTO_CONFIG)

    (ai_lite_v, ai_lite_f) = get_queue_counts(sqs, AI_QUEUE_LITE)
    (ai_basic_v, ai_basic_f) = get_queue_counts(sqs, AI_QUEUE_BASIC)
    ai_visible = ai_lite_v + ai_basic_v
    ai_in_flight = ai_lite_f + ai_basic_f
    video_count = get_visible_count(sqs, VIDEO_QUEUE)
    messaging_count = get_visible_count(sqs, MESSAGING_QUEUE)
    ai_total = ai_visible  # 메트릭은 visible만 (기존과 동일)

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

    set_ai_worker_asg_desired(autoscaling, ai_visible, ai_in_flight)

    logger.info(
        "queue_depth_metric | ai visible=%d in_flight=%d video=%d messaging=%d",
        ai_visible, ai_in_flight, video_count, messaging_count,
    )
    return {
        "ai_queue_depth": ai_total,
        "video_queue_depth": video_count,
        "messaging_queue_depth": messaging_count,
    }
