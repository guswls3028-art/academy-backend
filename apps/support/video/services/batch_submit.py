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


def submit_batch_job(video_job_id: str, duration_seconds: int | None = None) -> tuple[Optional[str], Optional[str]]:
    """
    VideoTranscodeJob에 대해 AWS Batch Job 제출.
    duration_seconds >= VIDEO_LONG_DURATION_THRESHOLD_SECONDS 이면 long 큐/JobDef 사용.

    Args:
        video_job_id: VideoTranscodeJob.id (UUID 문자열)
        duration_seconds: 비디오 길이(초). None이면 standard 큐 사용.

    Returns:
        (aws_job_id, None) 성공 시. (None, error_message) 실패 시.
    """
    from django.core.exceptions import ImproperlyConfigured

    if not getattr(settings, "VIDEO_BATCH_JOB_QUEUE", None):
        raise ImproperlyConfigured("VIDEO_BATCH_JOB_QUEUE is missing")
    if not getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", None):
        raise ImproperlyConfigured("VIDEO_BATCH_JOB_DEFINITION is missing")

    long_threshold = int(getattr(settings, "VIDEO_LONG_DURATION_THRESHOLD_SECONDS", 10800))
    use_long = duration_seconds is not None and duration_seconds >= long_threshold
    logger.info(
        "BATCH_SUBMIT_ROUTE | job_id=%s | duration_sec=%s | threshold=%s | use_long=%s",
        video_job_id, duration_seconds, long_threshold, use_long,
    )
    if use_long:
        queue_name = getattr(settings, "VIDEO_BATCH_JOB_QUEUE_LONG", "academy-v1-video-batch-long-queue")
        job_def_name = getattr(settings, "VIDEO_BATCH_JOB_DEFINITION_LONG", "academy-v1-video-batch-long-jobdef")
    else:
        queue_name = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "academy-v1-video-batch-queue")
        job_def_name = getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", "academy-v1-video-batch-jobdef")

    import boto3
    from botocore.exceptions import ClientError

    region = getattr(settings, "AWS_REGION", None) or getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2")

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
            "BATCH_SUBMIT | job_id=%s | aws_job_id=%s | queue=%s | long=%s",
            video_job_id, aws_job_id, queue_name, use_long,
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


def terminate_batch_job(video_job_id: str, reason: str = "superseded") -> bool:
    """
    AWS Batch Job 종료 (orphan 취소 또는 retry 시 이전 job 대체용).

    Args:
        video_job_id: VideoTranscodeJob.id (UUID 문자열). DB에서 aws_batch_job_id 조회.
        reason: Batch terminate_job reason.

    Returns:
        True if terminate was called (or job had no aws_batch_job_id), False on error.
    """
    from apps.support.video.models import VideoTranscodeJob

    job = VideoTranscodeJob.objects.filter(pk=video_job_id).first()
    if not job:
        return False
    aws_batch_job_id = (getattr(job, "aws_batch_job_id", None) or "").strip()
    if not aws_batch_job_id:
        return True

    region = getattr(settings, "AWS_REGION", None) or getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2")
    try:
        import boto3
        client = boto3.client("batch", region_name=region)
        client.terminate_job(jobId=aws_batch_job_id, reason=reason[:256])
        logger.info("BATCH_TERMINATE | job_id=%s aws_job_id=%s reason=%s", video_job_id, aws_batch_job_id, reason)
        return True
    except Exception as e:
        logger.warning("BATCH_TERMINATE_FAILED | job_id=%s aws_job_id=%s error=%s", video_job_id, aws_batch_job_id, e)
        return False
