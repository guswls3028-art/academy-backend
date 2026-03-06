# PATH: apps/support/video/management/commands/reconcile_batch_video_jobs.py
"""
Batch → DB 정합성 복구 (reconcile). Production-grade: Single-flight, conservative.

- Single-flight: Redis lock "video:reconcile:lock" (SETNX TTL=600s). Skip if lock fail.
- Conservative: DescribeJobs 실패 시 상태 변경 없음. not_found는 3회 연속 또는 created_at 30분 초과 시에만 fail.
- RUNNING 상태를 RETRY_WAIT로 덮어쓰지 않음. SUCCEEDED 상태는 절대 변경하지 않음.
- READY 전이는 worker(job_complete)만 수행. Reconcile은 READY를 만들지 않음 (stuck detection only).

Run via EventBridge → academy-video-ops-queue (rate 5 minutes).

옵션:
  --dry-run: DB 변경 없이 로그만
  --older-than-minutes: 이 시간보다 오래된 job만 대상 (기본 5)
  --resubmit: Batch FAILED 또는 not_found(fail 처리된 경우)에만 submit_batch_job 호출
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings

from apps.support.video.models import VideoTranscodeJob
from apps.support.video.services.batch_submit import submit_batch_job

logger = logging.getLogger(__name__)

REGION = getattr(settings, "AWS_DEFAULT_REGION", None) or __import__("os").environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
OLDER_THAN_MINUTES_DEFAULT = 5
VIDEO_BATCH_JOB_QUEUE = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "academy-v1-video-batch-queue")
RECONCILE_LOCK_KEY = "video:reconcile:lock"
RECONCILE_LOCK_TTL_SECONDS = 600
NOT_FOUND_COUNT_KEY_PREFIX = "video:reconcile:not_found:"
NOT_FOUND_COUNT_TTL_SECONDS = 3600
NOT_FOUND_CONSECUTIVE_THRESHOLD = 3
NOT_FOUND_MIN_AGE_MINUTES = 30
# Do not terminate RUNNABLE orphans until job has been RUNNABLE this long AND CE has scaled (desiredvCpus > 0)
RECONCILE_ORPHAN_MIN_RUNNABLE_MINUTES = getattr(settings, "RECONCILE_ORPHAN_MIN_RUNNABLE_MINUTES", 15)
# Set True to disable orphan terminate entirely (e.g. operator switch)
RECONCILE_ORPHAN_DISABLED = getattr(settings, "RECONCILE_ORPHAN_DISABLED", False)


def _acquire_reconcile_lock() -> bool:
    """Redis SETNX with TTL. Returns True if lock acquired."""
    try:
        from libs.redis.client import get_redis_client
        r = get_redis_client()
        if not r:
            logger.warning("reconcile: Redis not available, skipping lock (proceed at own risk)")
            return True
        # set(key, value, nx=True, ex=ttl) -> True if set, False if key exists
        ok = r.set(RECONCILE_LOCK_KEY, "1", nx=True, ex=RECONCILE_LOCK_TTL_SECONDS)
        return bool(ok)
    except Exception as e:
        logger.warning("reconcile: lock acquire failed: %s", e)
        return False


def _release_reconcile_lock() -> None:
    try:
        from libs.redis.client import get_redis_client
        r = get_redis_client()
        if r:
            r.delete(RECONCILE_LOCK_KEY)
    except Exception as e:
        logger.debug("reconcile: lock release failed: %s", e)


def _incr_not_found_count(job_id: str) -> int:
    """Increment consecutive not_found count for job. Returns new count."""
    try:
        from libs.redis.client import get_redis_client
        r = get_redis_client()
        if not r:
            return 0
        key = f"{NOT_FOUND_COUNT_KEY_PREFIX}{job_id}"
        n = r.incr(key)
        r.expire(key, NOT_FOUND_COUNT_TTL_SECONDS)
        return n
    except Exception as e:
        logger.debug("reconcile: not_found count incr failed: %s", e)
        return 0


def _reset_not_found_count(job_id: str) -> None:
    try:
        from libs.redis.client import get_redis_client
        r = get_redis_client()
        if r:
            r.delete(f"{NOT_FOUND_COUNT_KEY_PREFIX}{job_id}")
    except Exception as e:
        logger.debug("reconcile: not_found count reset failed: %s", e)


def _describe_jobs_boto3(aws_job_ids: list[str]) -> list:
    """
    boto3 describe_jobs. On any failure (AccessDenied, Throttling, etc.) raises.
    Caller must not change any DB state when this raises.
    """
    if not aws_job_ids:
        return []
    import boto3
    from botocore.exceptions import ClientError

    client = boto3.client("batch", region_name=REGION)
    resp = client.describe_jobs(jobs=aws_job_ids)
    return resp.get("jobs") or []


class Command(BaseCommand):
    help = "Reconcile DB with AWS Batch status (single-flight, conservative; no READY from reconcile)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not update DB, only log",
        )
        parser.add_argument(
            "--older-than-minutes",
            type=int,
            default=OLDER_THAN_MINUTES_DEFAULT,
            help=f"Only consider jobs updated longer than this ago (default: {OLDER_THAN_MINUTES_DEFAULT})",
        )
        parser.add_argument(
            "--resubmit",
            action="store_true",
            help="On Batch FAILED or not_found (after threshold), move to RETRY_WAIT and call submit_batch_job",
        )
        parser.add_argument(
            "--skip-lock",
            action="store_true",
            help="Skip Redis lock (for manual one-off run)",
        )

    def handle(self, *args, **options):
        from academy.adapters.db.django.repositories_video import (
            job_fail_retry,
            job_mark_dead,
            job_set_running,
        )
        from apps.support.video.services.batch_submit import terminate_batch_job

        dry_run = options["dry_run"]
        older_than_minutes = options["older_than_minutes"]
        resubmit = options["resubmit"]
        skip_lock = options["skip_lock"]
        cutoff = timezone.now() - timedelta(minutes=older_than_minutes)

        if not skip_lock and not _acquire_reconcile_lock():
            logger.info(
                "reconcile skipped - lock held",
                extra={"event": "reconcile_skipped", "reason": "lock_held"},
            )
            self.stdout.write("Reconcile skipped - lock held (another instance running).")
            return

        try:
            logger.info(
                "reconcile lock acquired, starting run",
                extra={"event": "reconcile_lock_acquired"},
            )
            self._run_reconcile(dry_run, resubmit, cutoff)
        finally:
            if not skip_lock:
                _release_reconcile_lock()

    def _run_reconcile(self, dry_run: bool, resubmit: bool, cutoff):
        from academy.adapters.db.django.repositories_video import (
            job_fail_retry,
            job_mark_dead,
            job_set_running,
        )
        from apps.support.video.services.batch_submit import terminate_batch_job
        from django.db.models import Count

        # ----- Duplicate active jobs: keep most recent, mark older DEAD -----
        dupes = (
            VideoTranscodeJob.objects.filter(
                state__in=[
                    VideoTranscodeJob.State.QUEUED,
                    VideoTranscodeJob.State.RUNNING,
                    VideoTranscodeJob.State.RETRY_WAIT,
                ],
            )
            .values("video_id")
            .annotate(n=Count("id"))
            .filter(n__gt=1)
        )
        for row in dupes:
            video_id = row["video_id"]
            jobs = list(
                VideoTranscodeJob.objects.filter(
                    video_id=video_id,
                    state__in=[
                        VideoTranscodeJob.State.QUEUED,
                        VideoTranscodeJob.State.RUNNING,
                        VideoTranscodeJob.State.RETRY_WAIT,
                    ],
                ).order_by("-created_at")
            )
            if len(jobs) <= 1:
                continue
            keep, older = jobs[0], jobs[1:]
            for job in older:
                if not dry_run:
                    if (job.aws_batch_job_id or "").strip():
                        terminate_batch_job(str(job.id), reason="reconcile_duplicate")
                    job_mark_dead(
                        str(job.id),
                        error_code="RECONCILE_DUPLICATE",
                        error_message="Reconcile: multiple active jobs for video, superseded by newer",
                    )
                self.stdout.write(f"RECONCILE duplicate job_id={job.id} video_id={video_id} marked DEAD")

        qs = (
            VideoTranscodeJob.objects.filter(
                state__in=[
                    VideoTranscodeJob.State.QUEUED,
                    VideoTranscodeJob.State.RUNNING,
                    VideoTranscodeJob.State.RETRY_WAIT,
                ],
            )
            .exclude(aws_batch_job_id="")
            .filter(updated_at__lt=cutoff)
            .select_related("video")
            .order_by("updated_at")[:50]
        )
        jobs = list(qs)
        if not jobs:
            self.stdout.write("No jobs to reconcile.")
            return

        aws_ids = [j.aws_batch_job_id for j in jobs]
        try:
            batch_jobs = _describe_jobs_boto3(aws_ids)
        except Exception as e:
            logger.warning(
                "reconcile: describe_jobs failed (no state changes): %s",
                e,
                extra={
                    "event": "reconcile_describe_jobs_failed",
                    "error": str(e)[:500],
                },
            )
            try:
                from apps.support.video.services.ops_events import emit_ops_event
                emit_ops_event(
                    "RECONCILE_DESCRIBE_JOBS_FAILED",
                    severity="WARNING",
                    payload={"error": str(e)[:500]},
                )
            except Exception:
                pass
            self.stdout.write(self.style.WARNING(f"Reconcile aborted: DescribeJobs failed. No DB changes. {e}"))
            return

        by_aws_id = {b["jobId"]: b for b in batch_jobs}
        updated = 0

        for job in jobs:
            aws_id = (job.aws_batch_job_id or "").strip()
            if not aws_id:
                continue
            bj = by_aws_id.get(aws_id)
            status = (bj or {}).get("status")
            status_reason = (bj or {}).get("statusReason") or ""

            if bj is not None:
                _reset_not_found_count(str(job.id))

            if status == "SUCCEEDED":
                # Reconcile does NOT change SUCCEEDED. READY is only set by worker (job_complete).
                logger.info(
                    "reconcile skip SUCCEEDED (worker owns READY)",
                    extra={"event": "reconcile_skip_succeeded", "job_id": str(job.id)},
                )
                self.stdout.write(f"RECONCILE skip SUCCEEDED job_id={job.id} (worker owns READY transition)")

            elif status == "FAILED":
                if not dry_run:
                    try:
                        from apps.support.video.services.ops_events import emit_ops_event
                        emit_ops_event("BATCH_DESYNC", severity="WARNING", tenant_id=job.tenant_id, video_id=job.video_id, job_id=str(job.id), aws_batch_job_id=aws_id, payload={"reason": "Batch FAILED", "status_reason": status_reason[:200]})
                    except Exception:
                        pass
                    job_fail_retry(str(job.id), status_reason or "Batch FAILED")
                    updated += 1
                    if resubmit:
                        dur = None
                        try:
                            if getattr(job, "video", None) and getattr(job.video, "duration", None):
                                dur = int(job.video.duration)
                        except Exception:
                            pass
                        aws_job_id, _ = submit_batch_job(str(job.id), duration_seconds=dur)
                        if aws_job_id:
                            job.aws_batch_job_id = aws_job_id
                            job.save(update_fields=["aws_batch_job_id", "updated_at"])
                self.stdout.write(f"RECONCILE fail_retry job_id={job.id} reason={status_reason[:100]}")

            elif status == "RUNNING" and job.state == VideoTranscodeJob.State.QUEUED:
                if not dry_run:
                    if job_set_running(str(job.id)):
                        updated += 1
                self.stdout.write(f"RECONCILE set_running job_id={job.id}")

            elif bj is None:
                # not_found: conservative. Do NOT overwrite RUNNING with RETRY_WAIT.
                if job.state == VideoTranscodeJob.State.RUNNING:
                    logger.info(
                        "reconcile not_found skip (DB RUNNING, do not overwrite)",
                        extra={
                            "event": "reconcile_not_found_skip",
                            "job_id": str(job.id),
                            "reason": "db_running",
                        },
                    )
                    self.stdout.write(f"RECONCILE skip not_found job_id={job.id} (DB RUNNING - do not overwrite)")
                    continue
                count = _incr_not_found_count(str(job.id))
                job_age_minutes = (timezone.now() - job.created_at).total_seconds() / 60
                allow_fail = count >= NOT_FOUND_CONSECUTIVE_THRESHOLD or job_age_minutes >= NOT_FOUND_MIN_AGE_MINUTES
                if not allow_fail:
                    logger.info(
                        "reconcile not_found defer (below threshold)",
                        extra={
                            "event": "reconcile_not_found_defer",
                            "job_id": str(job.id),
                            "not_found_count": count,
                            "age_minutes": round(job_age_minutes, 1),
                        },
                    )
                    self.stdout.write(f"RECONCILE skip not_found job_id={job.id} (count={count}, age_min={job_age_minutes:.0f})")
                    continue
                if not dry_run:
                    try:
                        from apps.support.video.services.ops_events import emit_ops_event
                        emit_ops_event("BATCH_DESYNC", severity="WARNING", tenant_id=job.tenant_id, video_id=job.video_id, job_id=str(job.id), aws_batch_job_id=aws_id, payload={"reason": "Batch job not found (after threshold)"})
                    except Exception:
                        pass
                    job_fail_retry(str(job.id), "Reconcile: Batch job not found (after threshold)")
                    updated += 1
                    if resubmit:
                        dur = None
                        try:
                            if getattr(job, "video", None) and getattr(job.video, "duration", None):
                                dur = int(job.video.duration)
                        except Exception:
                            pass
                        aws_job_id, _ = submit_batch_job(str(job.id), duration_seconds=dur)
                        if aws_job_id:
                            job.refresh_from_db()
                            job.aws_batch_job_id = aws_job_id
                            job.save(update_fields=["aws_batch_job_id", "updated_at"])
                logger.info(
                    "reconcile not_found fail (after threshold)",
                    extra={
                        "event": "reconcile_not_found_fail",
                        "job_id": str(job.id),
                        "aws_batch_job_id": aws_id,
                        "not_found_count": count,
                    },
                )
                self.stdout.write(f"RECONCILE not_found job_id={job.id} aws_id={aws_id} (count={count})")

        # ----- Orphan AWS jobs (video queue only) -----
        if RECONCILE_ORPHAN_DISABLED:
            logger.info(
                "reconcile orphan block skipped (RECONCILE_ORPHAN_DISABLED=True)",
                extra={"event": "reconcile_orphan_disabled"},
            )
            self.stdout.write("RECONCILE orphan block skipped (RECONCILE_ORPHAN_DISABLED=True).")
        else:
            self._run_orphan_terminate(dry_run)

        self.stdout.write(
            self.style.SUCCESS(f"Done: {updated} updated" + (" (dry-run)" if dry_run else ""))
        )

    def _run_orphan_terminate(self, dry_run: bool) -> None:
        """Terminate Batch jobs in video queue that have no DB row (orphans). Skips RUNNABLE jobs
        that are still pending scale-up (young or CE desiredvCpus=0)."""
        from datetime import datetime
        import boto3
        batch_client = boto3.client("batch", region_name=REGION)

        ce_desiredv_cpus = None
        try:
            q = batch_client.describe_job_queues(jobQueues=[VIDEO_BATCH_JOB_QUEUE])
            queues = q.get("jobQueues") or []
            if queues:
                order = (queues[0].get("computeEnvironmentOrder") or [])
                ce_arn = None
                for o in order:
                    if o.get("order") == 1:
                        ce_arn = o.get("computeEnvironment")
                        break
                if ce_arn:
                    ce_name = ce_arn.split("/")[-1] if "/" in ce_arn else ce_arn.split(":")[-1]
                    ce_desc = batch_client.describe_compute_environments(computeEnvironments=[ce_name])
                    ces = ce_desc.get("computeEnvironments") or []
                    if ces:
                        cr = ces[0].get("computeResources") or {}
                        ce_desiredv_cpus = cr.get("desiredvCpus", 0)
            if ce_desiredv_cpus is None:
                ce_desiredv_cpus = 0
        except Exception as e:
            logger.warning("reconcile: could not get CE desiredvCpus: %s", e)
            ce_desiredv_cpus = 0

        db_aws_ids = set(
            VideoTranscodeJob.objects.filter(
                state__in=[
                    VideoTranscodeJob.State.QUEUED,
                    VideoTranscodeJob.State.RUNNING,
                    VideoTranscodeJob.State.RETRY_WAIT,
                ],
            )
            .exclude(aws_batch_job_id="")
            .values_list("aws_batch_job_id", flat=True)
        )
        now = timezone.now()
        min_runnable_minutes = RECONCILE_ORPHAN_MIN_RUNNABLE_MINUTES

        try:
            paginator = batch_client.get_paginator("list_jobs")
            for status_filter in ["RUNNING", "RUNNABLE"]:
                for page in paginator.paginate(
                    jobQueue=VIDEO_BATCH_JOB_QUEUE,
                    jobStatus=status_filter,
                ):
                    for j in page.get("jobSummaryList") or []:
                        aws_id = j.get("jobId")
                        if not aws_id or aws_id in db_aws_ids:
                            continue
                        job_status = (j.get("status") or "").strip().upper()
                        created_at = j.get("createdAt")
                        runnable_age_minutes = None
                        if job_status == "RUNNABLE" and created_at:
                            try:
                                from django.utils import timezone as tz
                                if getattr(created_at, "tzinfo", None):
                                    created_dt = created_at
                                else:
                                    created_dt = tz.make_aware(created_at, tz.utc)
                                runnable_age_minutes = (now - created_dt).total_seconds() / 60.0
                            except Exception:
                                runnable_age_minutes = 0.0
                            skip_pending = (
                                runnable_age_minutes < min_runnable_minutes
                                or ce_desiredv_cpus == 0
                            )
                            if skip_pending:
                                logger.info(
                                    "reconcile skip orphan (RUNNABLE pending scale-up)",
                                    extra={
                                        "event": "reconcile_skip_orphan_pending_scale",
                                        "aws_batch_job_id": aws_id,
                                        "runnable_age_minutes": round(runnable_age_minutes, 1),
                                        "ce_desiredv_cpus": ce_desiredv_cpus,
                                        "min_runnable_minutes": min_runnable_minutes,
                                    },
                                )
                                self.stdout.write(
                                    f"RECONCILE skip orphan aws_id={aws_id} (RUNNABLE {runnable_age_minutes:.0f}min < {min_runnable_minutes}min or CE desiredvCpus={ce_desiredv_cpus})"
                                )
                                continue
                        if not dry_run:
                            try:
                                batch_client.terminate_job(jobId=aws_id, reason="reconcile_orphan")
                                logger.info(
                                    "reconcile orphan terminated",
                                    extra={
                                        "event": "reconcile_orphan_terminated",
                                        "aws_batch_job_id": aws_id,
                                        "job_status": job_status,
                                    },
                                )
                                try:
                                    from apps.support.video.services.ops_events import emit_ops_event
                                    emit_ops_event("ORPHAN_CANCELLED", severity="WARNING", aws_batch_job_id=aws_id, payload={"reason": "reconcile_orphan"})
                                except Exception:
                                    pass
                                self.stdout.write(self.style.WARNING(f"RECONCILE orphan terminated aws_id={aws_id}"))
                            except Exception as e:
                                logger.warning("terminate orphan %s failed: %s", aws_id, e)
                        else:
                            self.stdout.write(f"DRY-RUN RECONCILE would terminate orphan aws_id={aws_id}")
        except Exception as e:
            logger.warning("orphan cancel list/terminate failed: %s", e)
