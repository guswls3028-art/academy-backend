"""
Video Encoding - DB → Batch ONLY.

Upload Complete → Create VideoTranscodeJob → submit_batch_job → Exit.
NO SQS. NO queue. NO backlog.
"""

from __future__ import annotations

import logging
from typing import Optional

from apps.support.video.models import Video

logger = logging.getLogger(__name__)


def create_job_and_submit_batch(video: Video) -> Optional["VideoTranscodeJob"]:
    """
    Job 생성 + AWS Batch 제출.
    video.status must be UPLOADED.
    """
    from apps.support.video.models import VideoTranscodeJob
    from .batch_submit import submit_batch_job

    if video.status != Video.Status.UPLOADED:
        logger.error(
            "create_job_and_submit_batch: video %s status=%s (expected UPLOADED), skipped",
            video.id, video.status,
        )
        return None
    try:
        tenant = video.session.lecture.tenant
        tenant_id = int(tenant.id)
    except Exception as e:
        logger.error("create_job_and_submit_batch: Cannot get tenant for video %s, error=%s", video.id, e)
        return None

    job = VideoTranscodeJob.objects.create(
        video=video,
        tenant_id=tenant_id,
        state=VideoTranscodeJob.State.QUEUED,
    )
    video.current_job_id = job.id
    video.save(update_fields=["current_job_id", "updated_at"])

    aws_job_id, submit_error = submit_batch_job(str(job.id))
    if aws_job_id:
        job.aws_batch_job_id = aws_job_id
        job.save(update_fields=["aws_batch_job_id", "updated_at"])
        return job
    # submit 실패: job 삭제하지 않고 state/error 저장 (디버깅용)
    job.state = VideoTranscodeJob.State.FAILED
    job.error_code = "BATCH_SUBMIT_FAILED"
    job.error_message = submit_error or "submit_batch_job returned None"
    job.save(update_fields=["state", "error_code", "error_message", "updated_at"])
    video.current_job_id = None
    video.save(update_fields=["current_job_id", "updated_at"])
    return None
