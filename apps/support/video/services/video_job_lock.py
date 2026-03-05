"""
DynamoDB 기반 Video 1건당 Batch Job 1개 보장 락.

- PK: video_id (문자열). TTL 속성으로 long(12h) 기준 자동 만료.
- acquire: video_id당 1회만 성공 (ConditionExpression attribute_not_exists).
- heartbeat 시 extend로 lease 연장.
- READY/실패 정리 시 release(DeleteItem).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


def _table_name() -> str:
    return getattr(settings, "VIDEO_JOB_LOCK_TABLE_NAME", "academy-v1-video-job-lock")


def _ttl_seconds() -> int:
    return int(getattr(settings, "VIDEO_JOB_LOCK_TTL_SECONDS", "43200"))  # 12h for long


def _ttl_attribute() -> str:
    return getattr(settings, "VIDEO_JOB_LOCK_TTL_ATTRIBUTE", "ttl")


def acquire(video_id: int | str, job_id: str, ttl_seconds: Optional[int] = None) -> bool:
    """
    video_id에 대한 락 획득. 이미 항목이 있으면 실패 (1 video 1 job 보장).

    Returns:
        True if lock acquired, False if already locked (ConditionalCheckFailed).
    """
    import boto3
    from botocore.exceptions import ClientError

    table = _table_name()
    ttl_attr = _ttl_attribute()
    ttl_val = (ttl_seconds if ttl_seconds is not None else _ttl_seconds()) or _ttl_seconds()
    expiry = int(time.time()) + ttl_val
    key = str(video_id)

    try:
        client = boto3.client("dynamodb", region_name=getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2"))
        client.put_item(
            TableName=table,
            Item={
                "videoId": {"S": key},
                "jobId": {"S": str(job_id)},
                ttl_attr: {"N": str(expiry)},
            },
            ConditionExpression="attribute_not_exists(videoId)",
        )
        logger.info("VIDEO_JOB_LOCK_ACQUIRED | video_id=%s job_id=%s ttl_seconds=%s", key, job_id, ttl_val)
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info("VIDEO_JOB_LOCK_NOT_ACQUIRED | video_id=%s (already locked)", key)
            return False
        raise
    except Exception as e:
        logger.exception("VIDEO_JOB_LOCK_ACQUIRE_ERROR | video_id=%s error=%s", key, e)
        raise


def release(video_id: int | str) -> None:
    """락 해제 (READY/DEAD/실패 정리 시)."""
    import boto3

    table = _table_name()
    key = str(video_id)
    try:
        client = boto3.client("dynamodb", region_name=getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2"))
        client.delete_item(TableName=table, Key={"videoId": {"S": key}})
        logger.debug("VIDEO_JOB_LOCK_RELEASED | video_id=%s", key)
    except Exception as e:
        logger.warning("VIDEO_JOB_LOCK_RELEASE_ERROR | video_id=%s error=%s", key, e)


def extend(video_id: int | str, ttl_seconds: Optional[int] = None) -> bool:
    """
    heartbeat 시 lease 연장. 항목이 있을 때만 ttl 갱신.

    Returns:
        True if extended, False if item not found (no-op).
    """
    import boto3
    from botocore.exceptions import ClientError

    table = _table_name()
    ttl_attr = _ttl_attribute()
    ttl_val = (ttl_seconds if ttl_seconds is not None else _ttl_seconds()) or _ttl_seconds()
    expiry = int(time.time()) + ttl_val
    key = str(video_id)

    try:
        client = boto3.client("dynamodb", region_name=getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2"))
        client.update_item(
            TableName=table,
            Key={"videoId": {"S": key}},
            UpdateExpression=f"SET #{ttl_attr} = :ttl",
            ExpressionAttributeNames={f"#{ttl_attr}": ttl_attr},
            ExpressionAttributeValues={":ttl": {"N": str(expiry)}},
            ConditionExpression="attribute_exists(videoId)",
        )
        logger.debug("VIDEO_JOB_LOCK_EXTENDED | video_id=%s ttl_seconds=%s", key, ttl_val)
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise
    except Exception as e:
        logger.warning("VIDEO_JOB_LOCK_EXTEND_ERROR | video_id=%s error=%s", key, e)
        return False
