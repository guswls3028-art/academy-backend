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


def submit_batch_job(video_job_id: str) -> Optional[str]:
    """
    VideoTranscodeJob에 대해 AWS Batch Job 제출.

    Args:
        video_job_id: VideoTranscodeJob.id (UUID 문자열)

    Returns:
        AWS Batch job ID 문자열 또는 실패 시 None
    """
    import boto3
    from botocore.exceptions import ClientError

    region = getattr(settings, "AWS_REGION", None) or getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2")
    queue_name = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "academy-video-batch-queue")
    job_def_name = getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", "academy-video-batch-jobdef")

    try:
        client = boto3.client("batch", region_name=region)
        resp = client.submit_job(
            jobName=f"video-{video_job_id[:8]}",
            jobQueue=queue_name,
            jobDefinition=job_def_name,
            parameters={"job_id": str(video_job_id)},
        )
        aws_job_id = resp.get("jobId")
        logger.info(
            "BATCH_SUBMIT | job_id=%s | aws_job_id=%s | queue=%s",
            video_job_id, aws_job_id, queue_name,
        )
        return aws_job_id
    except ClientError as e:
        logger.exception(
            "BATCH_SUBMIT_FAILED | job_id=%s | error=%s",
            video_job_id, e,
        )
        return None
    except Exception as e:
        logger.exception(
            "BATCH_SUBMIT_ERROR | job_id=%s | error=%s",
            video_job_id, e,
        )
        return None
