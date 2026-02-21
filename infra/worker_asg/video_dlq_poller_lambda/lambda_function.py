"""
academy-video-jobs-dlq Poller Lambda.

- EventBridge rate(2 minutes)로 호출.
- DLQ 메시지에서 job_id 추출 → job_mark_dead(job_id) 호출 (API via VIDEO_BACKLOG_API_URL).
- 메시지 처리 후 DeleteMessage (DLQ 정리).
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
VIDEO_DLQ = os.environ.get("VIDEO_DLQ", "academy-video-jobs-dlq")
API_BASE = os.environ.get("VIDEO_BACKLOG_API_URL", "").rstrip("/")
LAMBDA_INTERNAL_API_KEY = os.environ.get("LAMBDA_INTERNAL_API_KEY", "")

BOTO_CONFIG = Config(retries={"max_attempts": 3, "mode": "standard"})


def _call_dlq_mark_dead(job_id: str) -> bool:
    """POST /api/v1/internal/video/dlq-mark-dead/ with job_id."""
    if not API_BASE:
        logger.warning("VIDEO_BACKLOG_API_URL not set; cannot call job_mark_dead")
        return False
    url = f"{API_BASE}/api/v1/internal/video/dlq-mark-dead/"
    headers = {"Content-Type": "application/json"}
    if LAMBDA_INTERNAL_API_KEY:
        headers["X-Internal-Key"] = LAMBDA_INTERNAL_API_KEY
    try:
        body = json.dumps({"job_id": job_id}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 202)
    except Exception as e:
        logger.warning("dlq-mark-dead API failed job_id=%s: %s", job_id, e)
        return False


def lambda_handler(event: dict, context: Any) -> dict:
    sqs = boto3.client("sqs", region_name=REGION, config=BOTO_CONFIG)

    try:
        url_resp = sqs.get_queue_url(QueueName=VIDEO_DLQ)
        queue_url = url_resp["QueueUrl"]
    except Exception as e:
        logger.warning("get_queue_url failed for %s: %s", VIDEO_DLQ, e)
        return {"processed": 0, "error": str(e)}

    processed = 0
    max_messages = 10

    resp = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=max_messages,
        WaitTimeSeconds=0,
        VisibilityTimeout=30,
    )

    for msg in resp.get("Messages", []):
        receipt_handle = msg.get("ReceiptHandle")
        body_str = msg.get("Body") or "{}"
        try:
            body = json.loads(body_str)
        except json.JSONDecodeError:
            logger.warning("DLQ message not JSON: %s", body_str[:200])
            continue

        job_id = body.get("job_id")
        if not job_id:
            logger.info("DLQ message has no job_id; body keys=%s", list(body.keys()))
            continue

        if _call_dlq_mark_dead(job_id):
            try:
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                processed += 1
                logger.info("DLQ processed and deleted | job_id=%s", job_id)
            except Exception as e:
                logger.warning("delete_message failed job_id=%s: %s", job_id, e)

    logger.info("DLQ poller done | processed=%d", processed)
    return {"processed": processed}
