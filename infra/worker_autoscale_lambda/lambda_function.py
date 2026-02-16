"""
Worker EC2 Autoscale (Wake) Lambda — 500 스케일 최적.

- EventBridge rate(1 minute)로 호출.
- Visible depth >= 1 이면 non-stopped 없을 때만 해당 타입 stopped 1대 Start.
- Fail-closed: SQS 조회 실패 시 raise. Cooldown 120초(SSM Parameter Store).

설계: docs/SSOT_0215/IMPORTANT/WORKER_AUTOSCALING_500_PLAN.md
"""
from __future__ import annotations

import os
import logging
import time
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
AI_QUEUE_LITE = os.environ.get("AI_QUEUE_LITE", "academy-ai-jobs-lite")
AI_QUEUE_BASIC = os.environ.get("AI_QUEUE_BASIC", "academy-ai-jobs-basic")
VIDEO_QUEUE = os.environ.get("VIDEO_QUEUE", "academy-video-jobs")
AI_WORKER_NAME_TAG = os.environ.get("AI_WORKER_NAME_TAG", "academy-ai-worker-cpu")
VIDEO_WORKER_NAME_TAG = os.environ.get("VIDEO_WORKER_NAME_TAG", "academy-video-worker")
MIN_MESSAGES = int(os.environ.get("MIN_MESSAGES_TO_START", "1"))
MAX_INSTANCES_PER_TYPE = int(os.environ.get("MAX_INSTANCES_PER_TYPE", "1"))
COOLDOWN_SECONDS = int(os.environ.get("COOLDOWN_SECONDS", "120"))
SSM_PREFIX = os.environ.get("SSM_PARAMETER_PREFIX", "/academy/worker-autoscale")

BOTO_CONFIG = Config(retries={"max_attempts": 5, "mode": "standard"})
NON_STOPPED_STATES = {"pending", "running", "stopping"}


@dataclass
class ScalingState:
    """미래 DynamoDB 이전용 구조. 500에서는 메모리만 사용."""
    desired: int = 0
    last_start_time: float = 0.0
    cooldown_until: float = 0.0


def get_capacity(worker_type: str) -> float:
    """Hook: 500에서는 항상 1. 10K+에서 moving average 등으로 교체."""
    _ = worker_type
    return 1.0


def calculate_desired(visible_depth: int, _capacity: float = 1.0) -> int:
    """Hook: 500에서는 visible >= 1 이면 1, 아니면 0. 10K+에서 수식 확장."""
    return 1 if visible_depth >= MIN_MESSAGES else 0


def get_queue_depth_visible(sqs_client, queue_name: str) -> int:
    """Visible 메시지 수만 반환. 실패 시 raise (Fail-closed)."""
    try:
        url = sqs_client.get_queue_url(QueueName=queue_name)["QueueUrl"]
        attrs = sqs_client.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        return int(attrs.get("Attributes", {}).get("ApproximateNumberOfMessages", 0))
    except Exception as e:
        logger.exception("SQS get_queue_depth_visible failed for %s", queue_name)
        raise RuntimeError(f"SQS queue depth failed: {queue_name}") from e


def list_instances_by_name(ec2_client, name_tag: str) -> list[dict]:
    """Name 태그로 전체 조회, NextToken 페이지네이션."""
    instances = []
    token = None
    while True:
        kwargs = {"Filters": [{"Name": "tag:Name", "Values": [name_tag]}]}
        if token:
            kwargs["NextToken"] = token
        resp = ec2_client.describe_instances(**kwargs)
        for r in resp.get("Reservations", []):
            instances.extend(r.get("Instances", []))
        token = resp.get("NextToken")
        if not token:
            break
    return instances


def has_non_stopped(instances: list[dict]) -> bool:
    return any(
        i.get("State", {}).get("Name") in NON_STOPPED_STATES
        for i in instances
    )


def get_stopped_ids(instances: list[dict], limit: int) -> list[str]:
    stopped = [i["InstanceId"] for i in instances if i.get("State", {}).get("Name") == "stopped"]
    return stopped[:limit]


def cooldown_active(ssm_client, param_name: str) -> bool:
    """Cooldown 구간이면 True."""
    try:
        resp = ssm_client.get_parameter(Name=param_name, WithDecryption=False)
        val = float(resp["Parameter"]["Value"])
        return (time.time() - val) < COOLDOWN_SECONDS
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return False
        logger.warning("Cooldown read failed for %s: %s", param_name, e)
        return False
    except Exception as e:
        logger.warning("Cooldown read failed for %s: %s", param_name, e)
        return False


def set_cooldown(ssm_client, param_name: str) -> None:
    """Start 후 현재 시각 저장 (Parameter 타입 String)."""
    try:
        ssm_client.put_parameter(
            Name=param_name,
            Value=str(int(time.time())),
            Type="String",
            Overwrite=True,
        )
    except Exception as e:
        logger.warning("Cooldown write failed for %s: %s", param_name, e)


def count_running(instances: list[dict]) -> int:
    return sum(1 for i in instances if i.get("State", {}).get("Name") in NON_STOPPED_STATES)


def maybe_start_worker(
    ec2_client,
    ssm_client,
    name_tag: str,
    param_name: str,
    desired: int,
) -> tuple[list[str], str, int]:
    """
    desired >= 1 이고 non-stopped 없고 cooldown 아님 이면 stopped 1대 Start.
    반환: (started_instance_ids, skip_reason, running_count)
    """
    if desired < 1:
        instances = list_instances_by_name(ec2_client, name_tag)
        return [], "desired_zero", count_running(instances)

    if cooldown_active(ssm_client, param_name):
        instances = list_instances_by_name(ec2_client, name_tag)
        return [], "cooldown", count_running(instances)

    instances = list_instances_by_name(ec2_client, name_tag)
    running_count = count_running(instances)
    if has_non_stopped(instances):
        return [], "non_stopped_exists", running_count

    to_start = get_stopped_ids(instances, MAX_INSTANCES_PER_TYPE)
    if not to_start:
        return [], "no_stopped", running_count

    ec2_client.start_instances(InstanceIds=to_start)
    set_cooldown(ssm_client, param_name)
    return to_start, "", running_count + len(to_start)


def lambda_handler(event: dict, context: Any) -> dict:
    sqs = boto3.client("sqs", region_name=REGION, config=BOTO_CONFIG)
    ec2 = boto3.client("ec2", region_name=REGION, config=BOTO_CONFIG)
    ssm = boto3.client("ssm", region_name=REGION, config=BOTO_CONFIG)

    # Fail-closed: SQS 조회 실패 시 raise
    ai_visible = get_queue_depth_visible(sqs, AI_QUEUE_LITE) + get_queue_depth_visible(sqs, AI_QUEUE_BASIC)
    video_visible = get_queue_depth_visible(sqs, VIDEO_QUEUE)

    capacity_ai = get_capacity("ai")
    capacity_video = get_capacity("video")

    state_ai = ScalingState(desired=calculate_desired(ai_visible, capacity_ai))
    state_video = ScalingState(desired=calculate_desired(video_visible, capacity_video))

    started_ai, skip_ai, running_ai = maybe_start_worker(
        ec2, ssm, AI_WORKER_NAME_TAG, f"{SSM_PREFIX}/last_start_ai", state_ai.desired
    )
    started_video, skip_video, running_video = maybe_start_worker(
        ec2, ssm, VIDEO_WORKER_NAME_TAG, f"{SSM_PREFIX}/last_start_video", state_video.desired
    )

    # 구조화 로그 (8요소: queue_depth, running_count, started_instances, skip_reason)
    log_payload = {
        "ai": {"queue_depth": ai_visible, "running_count": running_ai, "started_instances": started_ai, "skip_reason": skip_ai or "started"},
        "video": {"queue_depth": video_visible, "running_count": running_video, "started_instances": started_video, "skip_reason": skip_video or "started"},
    }
    logger.info("worker_autoscale | %s", log_payload)

    return {
        "ai_queue_depth": ai_visible,
        "video_queue_depth": video_visible,
        "started_ai": started_ai,
        "started_video": started_video,
        "running_count_ai": running_ai,
        "running_count_video": running_video,
        "skip_reason_ai": skip_ai or "started",
        "skip_reason_video": skip_video or "started",
    }
