"""
Video Encoding - DB → Batch ONLY.

Upload Complete → Create VideoTranscodeJob → submit_batch_job → Exit.
NO SQS. NO queue. NO backlog.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction

from apps.support.video.models import Video

logger = logging.getLogger(__name__)


def create_job_and_submit_batch(video: Video) -> Optional["VideoTranscodeJob"]:
    """
    Job 생성 + AWS Batch 제출.
    video.status must be UPLOADED.
    Idempotent: if video already has active job (QUEUED/RUNNING/RETRY_WAIT), return it.
    Uses select_for_update on video to prevent duplicate submit under concurrency.
    Transactional: on submit failure, rollback job creation.
    """
    from apps.support.video.models import VideoTranscodeJob
    from .batch_submit import submit_batch_job

    with transaction.atomic():
        video = Video.objects.select_for_update().filter(pk=video.pk).first()
        if not video:
            return None
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

        # Idempotency: return existing active job if any (after lock)
        existing = VideoTranscodeJob.objects.filter(
            video=video,
            state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT],
        ).first()
        if existing:
            logger.info("create_job_and_submit_batch: video %s already has active job %s", video.id, existing.id)
            return existing

        from django.conf import settings
        tenant_limit = int(getattr(settings, "VIDEO_TENANT_MAX_CONCURRENT", 2))
        global_limit = int(getattr(settings, "VIDEO_GLOBAL_MAX_CONCURRENT", 20))
        per_video_limit = int(getattr(settings, "VIDEO_MAX_JOBS_PER_VIDEO", 10))

        tenant_active = VideoTranscodeJob.objects.filter(
            tenant_id=tenant_id,
            state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT],
        ).count()
        if tenant_active >= tenant_limit:
            from apps.support.video.services.ops_events import emit_ops_event
            emit_ops_event(
                "TENANT_LIMIT_EXCEEDED",
                severity="WARNING",
                tenant_id=tenant_id,
                video_id=video.id,
                payload={"tenant_active": tenant_active, "limit": tenant_limit},
            )
            logger.warning("create_job_and_submit_batch: tenant %s active=%s >= %s", tenant_id, tenant_active, tenant_limit)
            return None

        global_active = VideoTranscodeJob.objects.filter(
            state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT],
        ).count()
        if global_active >= global_limit:
            logger.warning("create_job_and_submit_batch: global active=%s >= %s", global_active, global_limit)
            return None

        video_job_count = VideoTranscodeJob.objects.filter(video=video).count()
        if video_job_count >= per_video_limit:
            logger.warning("create_job_and_submit_batch: video %s total jobs=%s >= %s", video.id, video_job_count, per_video_limit)
            return None

        try:
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

            # Submit failed: rollback job creation (do not leave job without aws_batch_job_id)
            raise RuntimeError(submit_error or "submit_batch_job returned None")
        except RuntimeError:
            logger.exception("create_job_and_submit_batch: submit failed for video %s", video.id)
            return None
        except Exception:
            raise
