# PATH: apps/support/video/management/commands/validate_video_system.py
"""
System validation: consistency checks for video encoding state.

Checks:
- EventBridge rules academy-reconcile-video-jobs, academy-video-scan-stuck-rate exist, ENABLED, target Batch SubmitJob, JobQueue matches
- No RUNNING job without recent heartbeat
- No READY video without HLS prefix (hls_path set)
- No duplicate active jobs per video (DB constraint enforces; report violations)
- No orphan AWS Batch job without DB entry
"""

from __future__ import annotations

import logging
import sys
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import models
from django.utils import timezone
from django.conf import settings

from apps.support.video.models import Video, VideoTranscodeJob

logger = logging.getLogger(__name__)

HEARTBEAT_STALE_MINUTES = 5
REGION = getattr(settings, "AWS_DEFAULT_REGION", None) or __import__("os").environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
VIDEO_BATCH_JOB_QUEUE = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "academy-v1-video-batch-queue")
RECONCILE_RULE_NAME = "academy-reconcile-video-jobs"
SCAN_STUCK_RULE_NAME = "academy-video-scan-stuck-rate"


class Command(BaseCommand):
    help = "Validate video encoding system consistency"

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true", help="Attempt to fix issues (e.g. mark stale RUNNING as retry)")
        parser.add_argument("--dry-run", action="store_true", help="With --fix: only report what would be done")
        parser.add_argument("--heartbeat-minutes", type=int, default=HEARTBEAT_STALE_MINUTES)

    def handle(self, *args, **options):
        fix = options["fix"]
        dry_run = options.get("dry_run", False)
        heartbeat_minutes = options["heartbeat_minutes"]
        cutoff = timezone.now() - timedelta(minutes=heartbeat_minutes)
        errors = []

        # 0. EventBridge scheduler rules
        try:
            import boto3
            events = boto3.client("events", region_name=REGION)
            batch = boto3.client("batch", region_name=REGION)
            queue_arn = None
            try:
                q = batch.describe_job_queues(jobQueues=[VIDEO_BATCH_JOB_QUEUE])
                if q.get("jobQueues"):
                    queue_arn = q["jobQueues"][0].get("jobQueueArn")
            except Exception:
                pass
            for rule_name in [RECONCILE_RULE_NAME, SCAN_STUCK_RULE_NAME]:
                try:
                    r = events.describe_rule(Name=rule_name)
                    if r.get("State") != "ENABLED":
                        errors.append(f"EventBridge rule {rule_name} state is {r.get('State')} (expected ENABLED)")
                        self.stdout.write(self.style.ERROR(f"ERROR: EventBridge rule {rule_name} not ENABLED"))
                    targets = events.list_targets_by_rule(Rule=rule_name).get("Targets") or []
                    if not targets:
                        errors.append(f"EventBridge rule {rule_name} has no targets")
                        self.stdout.write(self.style.ERROR(f"ERROR: EventBridge rule {rule_name} has no target"))
                    else:
                        t = targets[0]
                        if "BatchParameters" not in t:
                            errors.append(f"EventBridge rule {rule_name} target is not Batch SubmitJob")
                            self.stdout.write(self.style.ERROR(f"ERROR: EventBridge rule {rule_name} target is not Batch SubmitJob"))
                        else:
                            jd = (t.get("BatchParameters") or {}).get("JobDefinition") or ""
                            jd_base = jd.split(":")[0] if jd else ""
                            expected_jd = "academy-video-ops-reconcile" if rule_name == RECONCILE_RULE_NAME else "academy-video-ops-scanstuck"
                            if jd_base != expected_jd:
                                errors.append(f"EventBridge rule {rule_name} target JobDefinition={jd_base} (expected {expected_jd})")
                                self.stdout.write(self.style.ERROR(f"ERROR: EventBridge rule {rule_name} target JobDefinition mismatch (got {jd_base})"))
                        if queue_arn and t.get("Arn") != queue_arn:
                            errors.append(f"EventBridge rule {rule_name} target JobQueue does not match settings (expected {VIDEO_BATCH_JOB_QUEUE})")
                            self.stdout.write(self.style.ERROR(f"ERROR: EventBridge rule {rule_name} JobQueueName mismatch"))
                except Exception as e:
                    err = getattr(e, "response", {}) or {}
                    if err.get("Error", {}).get("Code") == "ResourceNotFoundException":
                        errors.append(f"EventBridge rule {rule_name} does not exist")
                        self.stdout.write(self.style.ERROR(f"ERROR: EventBridge rule {rule_name} does not exist"))
                    else:
                        errors.append(f"EventBridge rule {rule_name}: {e}")
                        self.stdout.write(self.style.ERROR(f"ERROR: EventBridge {rule_name}: {e}"))
        except Exception as e:
            errors.append(f"EventBridge/Batch check failed: {e}")
            self.stdout.write(self.style.ERROR(f"ERROR: {e}"))

        # 1. RUNNING job without recent heartbeat
        running_stale = list(VideoTranscodeJob.objects.filter(
            state=VideoTranscodeJob.State.RUNNING,
            last_heartbeat_at__lt=cutoff,
        ).values_list("id", "video_id", "last_heartbeat_at", "attempt_count"))
        for job_id, video_id, hb, _ in running_stale:
            errors.append(f"RUNNING without heartbeat: job_id={job_id} video_id={video_id} last_heartbeat={hb}")
        if running_stale:
            self.stdout.write(self.style.WARNING(f"RUNNING without heartbeat: {len(running_stale)} jobs"))
        if fix and running_stale and not dry_run:
            from academy.adapters.db.django.repositories_video import job_mark_dead, job_fail_retry
            from apps.support.video.services.batch_submit import submit_batch_job
            STUCK_MAX_ATTEMPTS = 5
            for job_id, video_id, hb, attempt_count in running_stale:
                job = VideoTranscodeJob.objects.filter(pk=job_id).first()
                if not job:
                    continue
                attempt_after = (attempt_count or 0) + 1
                if attempt_after >= STUCK_MAX_ATTEMPTS:
                    job_mark_dead(str(job_id), error_code="VALIDATE_FIX_STUCK", error_message=f"RUNNING without heartbeat >{heartbeat_minutes}min")
                    self.stdout.write(self.style.SUCCESS(f"FIX: job_id={job_id} marked DEAD"))
                else:
                    job.state = VideoTranscodeJob.State.RETRY_WAIT
                    job.attempt_count = attempt_after
                    job.locked_by = ""
                    job.locked_until = None
                    job.save(update_fields=["state", "attempt_count", "locked_by", "locked_until", "updated_at"])
                    aws_job_id, _ = submit_batch_job(str(job_id))
                    if aws_job_id:
                        job.aws_batch_job_id = aws_job_id
                        job.save(update_fields=["aws_batch_job_id", "updated_at"])
                    self.stdout.write(self.style.SUCCESS(f"FIX: job_id={job_id} -> RETRY_WAIT + submit"))

        # 2. Video.status PROCESSING without active job
        processing_videos = list(Video.objects.filter(status=Video.Status.PROCESSING).select_related("current_job"))
        n_proc = 0
        for v in processing_videos:
            cur = v.current_job
            if not cur or cur.state not in (VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT):
                errors.append(f"PROCESSING without active job: video_id={v.id} current_job_id={v.current_job_id}")
                n_proc += 1
        if n_proc:
            self.stdout.write(self.style.WARNING(f"PROCESSING without active job: {n_proc} videos"))
        if fix and n_proc and not dry_run:
            for v in processing_videos:
                cur = v.current_job
                if not cur or cur.state not in (VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT):
                    Video.objects.filter(pk=v.id).update(
                        status=Video.Status.FAILED,
                        error_reason="validate_video_system: PROCESSING without active job",
                    )
                    self.stdout.write(self.style.SUCCESS(f"FIX: video_id={v.id} -> FAILED"))

        # 3. READY video without hls_path
        ready_no_hls = list(Video.objects.filter(status=Video.Status.READY).filter(hls_path="").values_list("id", flat=True))
        for vid in ready_no_hls:
            errors.append(f"READY without HLS path: video_id={vid}")
        if ready_no_hls:
            self.stdout.write(self.style.WARNING(f"READY without hls_path: {len(ready_no_hls)} videos"))

        # 4. Duplicate active jobs per video
        dupes = list(
            VideoTranscodeJob.objects.filter(
                state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT],
            )
            .values("video_id")
            .annotate(n=models.Count("id"))
            .filter(n__gt=1)
        )
        for row in dupes:
            errors.append(f"Duplicate active jobs: video_id={row['video_id']} count={row['n']}")
        if dupes:
            self.stdout.write(self.style.WARNING(f"Duplicate active jobs: {len(dupes)} videos"))
        if fix and dupes and not dry_run:
            from academy.adapters.db.django.repositories_video import job_mark_dead
            from apps.support.video.services.batch_submit import terminate_batch_job
            for row in dupes:
                video_id = row["video_id"]
                jobs = list(
                    VideoTranscodeJob.objects.filter(
                        video_id=video_id,
                        state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT],
                    ).order_by("-created_at")
                )
                if len(jobs) <= 1:
                    continue
                keep, older = jobs[0], jobs[1:]
                for job in older:
                    if (job.aws_batch_job_id or "").strip():
                        terminate_batch_job(str(job.id), reason="validate_video_system_duplicate")
                    job_mark_dead(str(job.id), error_code="VALIDATE_FIX_DUPLICATE", error_message="Duplicate active job; kept latest")
                    self.stdout.write(self.style.SUCCESS(f"FIX: job_id={job.id} video_id={video_id} DEAD (duplicate)"))

        # 5. Orphan AWS job
        db_aws_ids = set(
            VideoTranscodeJob.objects.filter(
                state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT],
            )
            .exclude(aws_batch_job_id="")
            .values_list("aws_batch_job_id", flat=True)
        )
        orphan_aws_ids = []
        try:
            import boto3
            client = boto3.client("batch", region_name=REGION)
            for status_filter in ["RUNNING", "RUNNABLE"]:
                paginator = client.get_paginator("list_jobs")
                for page in paginator.paginate(jobQueue=VIDEO_BATCH_JOB_QUEUE, jobStatus=status_filter):
                    for j in page.get("jobSummaryList") or []:
                        aws_id = j.get("jobId")
                        if aws_id and aws_id not in db_aws_ids:
                            errors.append(f"Orphan AWS job: aws_batch_job_id={aws_id}")
                            orphan_aws_ids.append(aws_id)
                            self.stdout.write(self.style.WARNING(f"Orphan AWS job: {aws_id}"))
            if fix and orphan_aws_ids and not dry_run:
                for aws_id in orphan_aws_ids:
                    try:
                        client.terminate_job(jobId=aws_id, reason="validate_video_system_orphan")
                        self.stdout.write(self.style.SUCCESS(f"FIX: terminated orphan aws_id={aws_id}"))
                    except Exception as e:
                        logger.warning("terminate orphan %s failed: %s", aws_id, e)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"list_jobs failed: {e}"))

        if not errors:
            self.stdout.write(self.style.SUCCESS("validate_video_system: OK"))
            return

        self.stdout.write(self.style.ERROR(f"validate_video_system: {len(errors)} issue(s)"))
        for e in errors[:50]:
            self.stdout.write(e)
        if len(errors) > 50:
            self.stdout.write(f"... and {len(errors) - 50} more")
        sys.exit(1)
