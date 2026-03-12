"""
Video Encoding - DB → Batch or Daemon.

Upload Complete → Create VideoTranscodeJob → DDB lock(video_id) → submit (Batch or daemon poll).
1 video 1 job: DynamoDB lock key=video_id, TTL 12h+, heartbeat로 lease 연장.

VIDEO_WORKER_MODE:
  "daemon" → Job만 DB 생성, Batch 제출 안 함. daemon_main이 폴링하여 처리.
  "batch"  → 기존대로 AWS Batch 제출 (기본값).
"""

from __future__ import annotations

import logging
from typing import NamedTuple, Optional

from django.db import transaction

from apps.support.video.models import Video

logger = logging.getLogger(__name__)


# Failure reason constants for create_job_and_submit_batch
REASON_TENANT_LIMIT = "tenant_limit"
REASON_GLOBAL_LIMIT = "global_limit"
REASON_PER_VIDEO_LIMIT = "per_video_limit"
REASON_SUBMIT_FAILED = "submit_failed"
REASON_NOT_UPLOADED = "not_uploaded"
REASON_NO_TENANT = "no_tenant"
REASON_NOT_FOUND = "not_found"


class JobResult(NamedTuple):
    job: Optional["VideoTranscodeJob"]
    reject_reason: Optional[str]


def create_job_and_submit_batch(video: Video) -> JobResult:
    """
    Job 생성 + AWS Batch 제출.
    video.status must be UPLOADED.
    Idempotent: DDB lock(video_id)로 1 video 1 job 보장. 기존 active job 있으면 해당 job 반환.

    Returns:
        JobResult(job, reject_reason). job is not None on success; reject_reason
        is not None when job creation was skipped (e.g. tenant_limit, submit_failed).
    """
    from apps.support.video.models import VideoTranscodeJob
    from .batch_submit import submit_batch_job
    from .video_job_lock import acquire as lock_acquire, release as lock_release
    from django.conf import settings

    lock_ttl = int(getattr(settings, "VIDEO_JOB_LOCK_TTL_SECONDS", 43200))

    with transaction.atomic():
        video = Video.objects.select_for_update().filter(pk=video.pk).first()
        if not video:
            return JobResult(None, REASON_NOT_FOUND)
        if video.status != Video.Status.UPLOADED:
            logger.error(
                "create_job_and_submit_batch: video %s status=%s (expected UPLOADED), skipped",
                video.id, video.status,
            )
            return JobResult(None, REASON_NOT_UPLOADED)
        try:
            tenant = video.session.lecture.tenant
            tenant_id = int(tenant.id)
        except Exception as e:
            logger.error("create_job_and_submit_batch: Cannot get tenant for video %s, error=%s", video.id, e)
            return JobResult(None, REASON_NO_TENANT)

        # Idempotency: return existing active job if any (after lock)
        existing = VideoTranscodeJob.objects.filter(
            video=video,
            state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT],
        ).first()
        if existing:
            logger.info("create_job_and_submit_batch: video %s already has active job %s", video.id, existing.id)
            return JobResult(existing, None)

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
            return JobResult(None, REASON_TENANT_LIMIT)

        global_active = VideoTranscodeJob.objects.filter(
            state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT],
        ).count()
        if global_active >= global_limit:
            logger.warning("create_job_and_submit_batch: global active=%s >= %s", global_active, global_limit)
            return JobResult(None, REASON_GLOBAL_LIMIT)

        video_job_count = VideoTranscodeJob.objects.filter(video=video).count()
        if video_job_count >= per_video_limit:
            logger.warning("create_job_and_submit_batch: video %s total jobs=%s >= %s", video.id, video_job_count, per_video_limit)
            return JobResult(None, REASON_PER_VIDEO_LIMIT)

        try:
            job = VideoTranscodeJob.objects.create(
                video=video,
                tenant_id=tenant_id,
                state=VideoTranscodeJob.State.QUEUED,
            )
            video.current_job_id = job.id
            video.save(update_fields=["current_job_id", "updated_at"])

            if not lock_acquire(video.id, str(job.id), ttl_seconds=lock_ttl):
                job.delete()
                existing_after = VideoTranscodeJob.objects.filter(
                    video=video,
                    state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT],
                ).first()
                if existing_after:
                    video.current_job_id = existing_after.id
                    video.save(update_fields=["current_job_id", "updated_at"])
                else:
                    video.current_job_id = None
                    video.save(update_fields=["current_job_id", "updated_at"])
                logger.info("create_job_and_submit_batch: video %s DDB lock failed, returning existing=%s", video.id, existing_after.id if existing_after else None)
                return JobResult(existing_after, None if existing_after else REASON_SUBMIT_FAILED)

            # Worker mode routing:
            # - daemon mode + duration <= 30min (or unknown): daemon polls DB
            # - daemon mode + duration > 30min: auto-fallback to Batch (daemon won't pick these up)
            # - batch mode: always submit to Batch
            worker_mode = getattr(settings, "VIDEO_WORKER_MODE", "batch")
            daemon_max_duration = int(getattr(settings, "DAEMON_MAX_DURATION_SECONDS", 1800))
            video_duration = video.duration or 0

            if worker_mode == "daemon" and video_duration <= daemon_max_duration:
                logger.info(
                    "create_job_and_submit_batch: DAEMON mode — job %s created, skipping Batch submit (video %s, duration=%s)",
                    job.id, video.id, video.duration,
                )
                return JobResult(job, None)

            if worker_mode == "daemon" and video_duration > daemon_max_duration:
                logger.info(
                    "create_job_and_submit_batch: DAEMON mode but duration %ss > %ss — fallback to Batch (video %s, job %s)",
                    video_duration, daemon_max_duration, video.id, job.id,
                )

            aws_job_id, submit_error = submit_batch_job(str(job.id), duration_seconds=video.duration if video.duration else None)
            if aws_job_id:
                job.aws_batch_job_id = aws_job_id
                job.save(update_fields=["aws_batch_job_id", "updated_at"])
                return JobResult(job, None)

            lock_release(video.id)
            raise RuntimeError(submit_error or "submit_batch_job returned None")
        except RuntimeError:
            logger.exception("create_job_and_submit_batch: submit failed for video %s", video.id)
            return JobResult(None, REASON_SUBMIT_FAILED)
        except Exception:
            raise
