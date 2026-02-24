# PATH: apps/support/video/management/commands/reconcile_batch_video_jobs.py
"""
Batch → DB 정합성 복구 (reconcile).

- Sync Batch → DB (describe_jobs)
- Detect: missing AWS job, SUCCEEDED but no READY, FAILED but DB RUNNING,
  long-running beyond timeout, duplicate active jobs per video
- Cancel orphan AWS jobs (Batch job with no DB active job)

Run every 120s via cron / EventBridge (see scripts/infra/eventbridge/reconcile_video_jobs_schedule.json):
  python manage.py reconcile_batch_video_jobs

옵션:
  --dry-run: DB 변경 없이 로그만
  --older-than-minutes: 이 시간보다 오래된 job만 대상 (기본 5)
  --resubmit: Batch FAILED/미조회 시 RETRY_WAIT로 보낸 뒤 submit_batch_job 호출
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

from django.conf import settings

REGION = getattr(settings, "AWS_DEFAULT_REGION", None) or __import__("os").environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
OLDER_THAN_MINUTES_DEFAULT = 5
VIDEO_BATCH_JOB_QUEUE = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "academy-video-batch-queue")


def _describe_jobs_boto3(aws_job_ids: list[str]):
    """boto3로 describe_jobs 호출. jobs 목록 반환 (없으면 빈 리스트)."""
    if not aws_job_ids:
        return []
    try:
        import boto3
        from botocore.exceptions import ClientError

        client = boto3.client("batch", region_name=REGION)
        resp = client.describe_jobs(jobs=aws_job_ids)
        return resp.get("jobs") or []
    except Exception as e:
        logger.warning("describe_jobs failed: %s", e)
        return []


class Command(BaseCommand):
    help = "Reconcile DB with AWS Batch status (describe_jobs → DB update)"

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
            help="On Batch FAILED or job not found, move to RETRY_WAIT and call submit_batch_job",
        )

    def handle(self, *args, **options):
        from academy.adapters.db.django.repositories_video import (
            job_complete,
            job_fail_retry,
            job_mark_dead,
            job_set_running,
        )
        from apps.support.video.services.batch_submit import terminate_batch_job

        dry_run = options["dry_run"]
        older_than_minutes = options["older_than_minutes"]
        resubmit = options["resubmit"]
        cutoff = timezone.now() - timedelta(minutes=older_than_minutes)

        # Duplicate active jobs per video: keep most recent, mark older DEAD and cancel AWS job
        from django.db.models import Count

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
        batch_jobs = _describe_jobs_boto3(aws_ids)
        by_aws_id = {b["jobId"]: b for b in batch_jobs}

        updated = 0
        for job in jobs:
            aws_id = (job.aws_batch_job_id or "").strip()
            if not aws_id:
                continue
            bj = by_aws_id.get(aws_id)
            status = (bj or {}).get("status")
            status_reason = (bj or {}).get("statusReason") or ""

            if status == "SUCCEEDED":
                video = getattr(job, "video", None)
                if video and getattr(video, "status", None) == "READY" and getattr(video, "hls_path", None):
                    if not dry_run:
                        ok, _ = job_complete(str(job.id), video.hls_path, getattr(video, "duration", None))
                        if ok:
                            updated += 1
                            self.stdout.write(self.style.SUCCESS(f"RECONCILE complete job_id={job.id}"))
                    else:
                        self.stdout.write(f"DRY-RUN RECONCILE complete job_id={job.id}")
                else:
                    if not dry_run:
                        job_fail_retry(str(job.id), "Reconcile: Batch SUCCEEDED, output missing")
                        updated += 1
                    self.stdout.write(f"RECONCILE fail_retry (no output) job_id={job.id}")

            elif status == "FAILED":
                if not dry_run:
                    from apps.support.video.services.ops_events import emit_ops_event
                    emit_ops_event("BATCH_DESYNC", severity="WARNING", tenant_id=job.tenant_id, video_id=job.video_id, job_id=str(job.id), aws_batch_job_id=aws_id, payload={"reason": "Batch FAILED", "status_reason": status_reason[:200]})
                    job_fail_retry(str(job.id), status_reason or "Batch FAILED")
                    updated += 1
                    if resubmit:
                        aws_job_id, _ = submit_batch_job(str(job.id))
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
                if not dry_run:
                    from apps.support.video.services.ops_events import emit_ops_event
                    emit_ops_event("BATCH_DESYNC", severity="WARNING", tenant_id=job.tenant_id, video_id=job.video_id, job_id=str(job.id), aws_batch_job_id=aws_id, payload={"reason": "Batch job not found"})
                    job_fail_retry(str(job.id), "Reconcile: Batch job not found")
                    updated += 1
                    if resubmit:
                        aws_job_id, _ = submit_batch_job(str(job.id))
                        if aws_job_id:
                            job.refresh_from_db()
                            job.aws_batch_job_id = aws_job_id
                            job.save(update_fields=["aws_batch_job_id", "updated_at"])
                self.stdout.write(f"RECONCILE not_found job_id={job.id} aws_id={aws_id}")

        # Orphan AWS jobs: list Batch jobs in queue (RUNNING/RUNNABLE), terminate those not in DB active set
        try:
            import boto3
            batch_client = boto3.client("batch", region_name=REGION)
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
            paginator = batch_client.get_paginator("list_jobs")
            for status_filter in ["RUNNING", "RUNNABLE"]:
                for page in paginator.paginate(
                    jobQueue=VIDEO_BATCH_JOB_QUEUE,
                    jobStatus=status_filter,
                ):
                    for j in page.get("jobSummaryList") or []:
                        aws_id = j.get("jobId")
                        if aws_id and aws_id not in db_aws_ids:
                            if not dry_run:
                                try:
                                    batch_client.terminate_job(jobId=aws_id, reason="reconcile_orphan")
                                    from apps.support.video.services.ops_events import emit_ops_event
                                    emit_ops_event("ORPHAN_CANCELLED", severity="WARNING", aws_batch_job_id=aws_id, payload={"reason": "reconcile_orphan"})
                                    self.stdout.write(self.style.WARNING(f"RECONCILE orphan terminated aws_id={aws_id}"))
                                except Exception as e:
                                    logger.warning("terminate orphan %s failed: %s", aws_id, e)
                            else:
                                self.stdout.write(f"DRY-RUN RECONCILE would terminate orphan aws_id={aws_id}")
        except Exception as e:
            logger.warning("orphan cancel list/terminate failed: %s", e)

        self.stdout.write(
            self.style.SUCCESS(f"Done: {updated} updated" + (" (dry-run)" if dry_run else ""))
        )
