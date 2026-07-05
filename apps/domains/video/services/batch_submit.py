"""
AWS Batch Video Job Submit Service

DB(VideoTranscodeJob) SSOT. API가 Job 생성 후 submit_batch_job 호출.
인코딩 경로: SQS 미사용, Batch만 사용.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings
from academy.adapters.compute.aws_batch import (
    AwsBatchClientError,
    submit_video_batch_job,
    terminate_batch_job,
)

logger = logging.getLogger(__name__)


def submit_batch_job(video_job_id: str, duration_seconds: int | None = None) -> tuple[Optional[str], Optional[str]]:
    """
    VideoTranscodeJob에 대해 AWS Batch Job 제출.

    short / long 분리는 폐기됨 (2026-05-10). 모든 영상이 c6g.4xlarge VCPU=8 + 병렬
    R2 업로드 파이프라인을 거치며, jobdef timeout(2시간)이 안전망. 4시간+ 영상이 들어오면
    timeout으로 자동 종료되고 reconcile/scan_stuck이 재시도.

    Args:
        video_job_id: VideoTranscodeJob.id (UUID 문자열)
        duration_seconds: (옛 long 라우팅용 인자, 현재는 관측만)

    Returns:
        (aws_job_id, None) 성공 시. (None, error_message) 실패 시.
    """
    from django.core.exceptions import ImproperlyConfigured

    if not getattr(settings, "VIDEO_BATCH_JOB_QUEUE", None):
        raise ImproperlyConfigured("VIDEO_BATCH_JOB_QUEUE is missing")
    if not getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", None):
        raise ImproperlyConfigured("VIDEO_BATCH_JOB_DEFINITION is missing")

    queue_name = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "academy-v1-video-batch-queue")
    job_def_name = getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", "academy-v1-video-batch-jobdef")
    logger.info(
        "BATCH_SUBMIT_ROUTE | job_id=%s | duration_sec=%s | queue=%s",
        video_job_id, duration_seconds, queue_name,
    )

    region = getattr(settings, "AWS_REGION", None) or getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2")

    try:
        aws_job_id = submit_video_batch_job(
            video_job_id=video_job_id,
            queue_name=queue_name,
            job_definition=job_def_name,
            region=region,
        )
        logger.info(
            "BATCH_SUBMIT | job_id=%s | aws_job_id=%s | queue=%s",
            video_job_id, aws_job_id, queue_name,
        )
        return (aws_job_id, None)
    except AwsBatchClientError as e:
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


def terminate_video_job(video_job_id: str, reason: str = "superseded") -> bool:
    """
    VideoTranscodeJob.id 로 DB lookup 후 AWS Batch terminate (orphan 취소 / retry 시 이전 job 대체).

    같은 도메인의 `batch_control.terminate_aws_batch_job` (raw `aws_batch_job_id` 받아 best-effort terminate
    + describe-first idempotency + ops_event 기록) 과 혼동 금지. 시그니처가 달라서 인자 순서를 잘못
    넘기면 무음 실패한다.

    Args:
        video_job_id: VideoTranscodeJob.id (UUID 문자열). DB에서 aws_batch_job_id 조회.
        reason: Batch terminate_job reason.

    Returns:
        True if terminate was called (or job had no aws_batch_job_id), False on error.
    """
    from apps.domains.video.models import VideoTranscodeJob

    job = VideoTranscodeJob.objects.filter(pk=video_job_id).first()
    if not job:
        return False
    aws_batch_job_id = (getattr(job, "aws_batch_job_id", None) or "").strip()
    if not aws_batch_job_id:
        return True

    region = getattr(settings, "AWS_REGION", None) or getattr(settings, "AWS_DEFAULT_REGION", "ap-northeast-2")
    try:
        terminate_batch_job(aws_batch_job_id=aws_batch_job_id, reason=reason, region=region)
        logger.info("BATCH_TERMINATE | job_id=%s aws_job_id=%s reason=%s", video_job_id, aws_batch_job_id, reason)
        return True
    except Exception as e:
        logger.warning("BATCH_TERMINATE_FAILED | job_id=%s aws_job_id=%s error=%s", video_job_id, aws_batch_job_id, e)
        return False
