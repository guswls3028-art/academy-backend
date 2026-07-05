"""
DynamoDB-backed lock for guaranteeing at most one Batch job per video.

Infrastructure details live in the adapter layer; legacy Django-domain callers
continue through apps.domains.video.services.video_job_lock.
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
    return int(getattr(settings, "VIDEO_JOB_LOCK_TTL_SECONDS", "43200"))  # 12h for long jobs


def _ttl_attribute() -> str:
    return getattr(settings, "VIDEO_JOB_LOCK_TTL_ATTRIBUTE", "ttl")


def _client():
    import boto3

    return boto3.client(
        "dynamodb",
        region_name=getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2"),
    )


def acquire(video_id: int | str, job_id: str, ttl_seconds: Optional[int] = None) -> bool:
    """
    Acquire a lock for a video. Returns False when an unexpired lock already exists.
    """
    from botocore.exceptions import ClientError

    table = _table_name()
    ttl_attr = _ttl_attribute()
    ttl_val = (ttl_seconds if ttl_seconds is not None else _ttl_seconds()) or _ttl_seconds()
    expiry = int(time.time()) + ttl_val
    key = str(video_id)
    now_epoch = int(time.time())

    try:
        _client().put_item(
            TableName=table,
            Item={
                "videoId": {"S": key},
                "jobId": {"S": str(job_id)},
                ttl_attr: {"N": str(expiry)},
            },
            # DynamoDB TTL deletion can lag, so a stale item may be overwritten.
            ConditionExpression="attribute_not_exists(videoId) OR #ttl < :now",
            ExpressionAttributeNames={"#ttl": ttl_attr},
            ExpressionAttributeValues={":now": {"N": str(now_epoch)}},
        )
        logger.info("VIDEO_JOB_LOCK_ACQUIRED | video_id=%s job_id=%s ttl_seconds=%s", key, job_id, ttl_val)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info("VIDEO_JOB_LOCK_NOT_ACQUIRED | video_id=%s (already locked, ttl still valid)", key)
            return False
        raise
    except Exception as exc:
        logger.exception("VIDEO_JOB_LOCK_ACQUIRE_ERROR | video_id=%s error=%s", key, exc)
        raise


def release(video_id: int | str) -> None:
    """Release a lock after READY/DEAD/failure cleanup."""
    key = str(video_id)
    try:
        _client().delete_item(TableName=_table_name(), Key={"videoId": {"S": key}})
        logger.debug("VIDEO_JOB_LOCK_RELEASED | video_id=%s", key)
    except Exception as exc:
        logger.warning("VIDEO_JOB_LOCK_RELEASE_ERROR | video_id=%s error=%s", key, exc)


def extend(video_id: int | str, ttl_seconds: Optional[int] = None) -> bool:
    """
    Extend a heartbeat lease. Returns False when the item no longer exists.
    """
    from botocore.exceptions import ClientError

    ttl_attr = _ttl_attribute()
    ttl_val = (ttl_seconds if ttl_seconds is not None else _ttl_seconds()) or _ttl_seconds()
    expiry = int(time.time()) + ttl_val
    key = str(video_id)

    try:
        _client().update_item(
            TableName=_table_name(),
            Key={"videoId": {"S": key}},
            UpdateExpression=f"SET #{ttl_attr} = :ttl",
            ExpressionAttributeNames={f"#{ttl_attr}": ttl_attr},
            ExpressionAttributeValues={":ttl": {"N": str(expiry)}},
            ConditionExpression="attribute_exists(videoId)",
        )
        logger.debug("VIDEO_JOB_LOCK_EXTENDED | video_id=%s ttl_seconds=%s", key, ttl_val)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise
    except Exception as exc:
        logger.warning("VIDEO_JOB_LOCK_EXTEND_ERROR | video_id=%s error=%s", key, exc)
        return False
