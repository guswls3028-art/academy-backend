"""
AWS Batch Video Job Submit Service

DB(VideoTranscodeJob) SSOT. API가 Job 생성 후 submit_batch_job 호출.
인코딩 경로: SQS 미사용, Batch만 사용.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


def submit_batch_job(video_job_id: str) -> tuple[Optional[str], Optional[str]]:
    """
    VideoTranscodeJob에 대해 AWS Batch Job 제출.

    Args:
        video_job_id: VideoTranscodeJob.id (UUID 문자열)

    Returns:
        (aws_job_id, None) 성공 시. (None, error_message) 실패 시.
        호출부에서 job.aws_batch_job_id 저장 또는 job.error_code/error_message 저장에 사용.

    Raises:
        ImproperlyConfigured: VIDEO_BATCH_JOB_QUEUE 또는 VIDEO_BATCH_JOB_DEFINITION 미설정 시
    """
    from django.core.exceptions import ImproperlyConfigured

    if not getattr(settings, "VIDEO_BATCH_JOB_QUEUE", None):
        raise ImproperlyConfigured("VIDEO_BATCH_JOB_QUEUE is missing")
    if not getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", None):
        raise ImproperlyConfigured("VIDEO_BATCH_JOB_DEFINITION is missing")

    import boto3
    from botocore.exceptions import ClientError

    region = getattr(settings, "AWS_REGION", None) or getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2")
    queue_name = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "academy-video-batch-queue")
    job_def_name = getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", "academy-video-batch-jobdef")

    # Ref::job_id 치환이 깨져도 env로 전달되도록 containerOverrides 사용 (관측/디버깅 강화)
    container_overrides = {
        "environment": [
            {"name": "VIDEO_JOB_ID", "value": str(video_job_id)},
        ],
    }

    try:
        client = boto3.client("batch", region_name=region)
        resp = client.submit_job(
            jobName=f"video-{video_job_id[:8]}",
            jobQueue=queue_name,
            jobDefinition=job_def_name,
            parameters={"job_id": str(video_job_id)},
            containerOverrides=container_overrides,
        )
        aws_job_id = resp.get("jobId")
        logger.info(
            "BATCH_SUBMIT | job_id=%s | aws_job_id=%s | queue=%s",
            video_job_id, aws_job_id, queue_name,
        )
        return (aws_job_id, None)
    except ClientError as e:
        err_msg = str(e)[:2000]
        logger.exception(
            "BATCH_SUBMIT_FAILED | job_id=%s | error=%s",
            video_job_id, e,
        )
        return (None, err_msg)
    except Exception as e:
        err_msg = str(e)[:2000]
        logger.exception(
            "BATCH_SUBMIT_ERROR | job_id=%s | error=%s",
            video_job_id, e,
        )
        return (None, err_msg)
