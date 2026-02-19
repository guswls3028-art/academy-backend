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
VIDEO_WORKER_ASG_NAME = os.environ.get("VIDEO_WORKER_ASG_NAME", "academy-video-worker-asg")
VIDEO_WORKER_ASG_MAX = int(os.environ.get("VIDEO_WORKER_ASG_MAX", "20"))
VIDEO_WORKER_ASG_MIN = int(os.environ.get("VIDEO_WORKER_ASG_MIN", "1"))
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


def set_asg_desired(
    autoscaling_client,
    asg_name: str,
    visible: int,
    in_flight: int,
    min_capacity: int,
    max_capacity: int,
    messages_per_instance: int = TARGET_MESSAGES_PER_INSTANCE,
    conservative_scale_in: bool = False,
) -> None:
    """워커 ASG desired capacity 조정. total=0이면 MIN, else min(MAX, max(MIN, ceil(total/N))). N 기본 20, Video는 1."""
    total_for_scale = visible + in_flight
    # SQS Approximate 타이밍 이슈: visible=0, in_flight=1일 때 실제로는 2개일 수 있음.
    # Video(1:1)에서 조기 scale-down 방지: visible=0이고 in_flight>0이면 total을 in_flight+1 이상으로 유지
    if conservative_scale_in and in_flight > 0 and visible == 0:
        total_for_scale = max(total_for_scale, in_flight + 1)
    if total_for_scale == 0:
        new_desired = min_capacity
    else:
        new_desired = min(
            max_capacity,
            max(min_capacity, math.ceil(total_for_scale / messages_per_instance)),
        )

    try:
        asgs = autoscaling_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name],
        )
        if not asgs.get("AutoScalingGroups"):
            logger.warning("ASG not found: %s", asg_name)
            return
        current = asgs["AutoScalingGroups"][0]["DesiredCapacity"]
        autoscaling_client.set_desired_capacity(
            AutoScalingGroupName=asg_name,
            DesiredCapacity=new_desired,
        )
        if current != new_desired:
            logger.info(
                "%s desired %s -> %s (visible=%d in_flight=%d)",
                asg_name, current, new_desired, visible, in_flight,
            )
    except Exception as e:
        logger.warning("set_asg_desired failed for %s: %s", asg_name, e)


def set_ai_worker_asg_desired(autoscaling_client, ai_visible: int, ai_in_flight: int) -> None:
    """AI 워커 상시 1대 대기. 큐 깊이에 따라 1~MAX 스케일, 0으로 스케일인 안 함."""
    set_asg_desired(autoscaling_client, AI_WORKER_ASG_NAME, ai_visible, ai_in_flight, 1, AI_WORKER_ASG_MAX)


def set_video_worker_asg_desired(autoscaling_client, video_visible: int, video_in_flight: int) -> None:
    """Video 워커: 1 instance = 1 video."""
    set_asg_desired(
        autoscaling_client,
        VIDEO_WORKER_ASG_NAME,
        video_visible,
        video_in_flight,
        VIDEO_WORKER_ASG_MIN,
        VIDEO_WORKER_ASG_MAX,
        messages_per_instance=1,
    )


def set_messaging_worker_asg_desired(autoscaling_client, messaging_visible: int, messaging_in_flight: int) -> None:
    """Messaging 워커 ASG desired capacity 조정."""
    set_asg_desired(autoscaling_client, MESSAGING_WORKER_ASG_NAME, messaging_visible, messaging_in_flight, MESSAGING_WORKER_ASG_MIN, MESSAGING_WORKER_ASG_MAX)


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
        ],
    )

    set_ai_worker_asg_desired(autoscaling, ai_visible, ai_in_flight)
    set_video_worker_asg_desired(autoscaling, video_visible, video_in_flight)
    set_messaging_worker_asg_desired(autoscaling, messaging_visible, messaging_in_flight)

    logger.info(
        "queue_depth_metric | ai visible=%d in_flight=%d video visible=%d in_flight=%d messaging visible=%d in_flight=%d",
        ai_visible, ai_in_flight, video_visible, video_in_flight, messaging_visible, messaging_in_flight,
    )
    return {
        "ai_queue_depth": ai_total,
        "video_queue_depth": video_visible,
        "messaging_queue_depth": messaging_visible,
    }
