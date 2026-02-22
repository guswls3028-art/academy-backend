# PATH: apps/support/video/management/commands/scan_stuck_video_jobs.py
"""
Stuck Scanner: RUNNING인데 last_heartbeat_at < now - 3분 → RETRY_WAIT, attempt_count++.

attempt_count >= MAX 이면 DEAD 처리.
RETRY_WAIT 전환 시 submit_batch_job 호출 (Batch 재제출).

Run via cron (e.g. every 2 min):
  python manage.py scan_stuck_video_jobs
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from apps.support.video.models import VideoTranscodeJob
from apps.support.video.services.batch_submit import submit_batch_job


STUCK_THRESHOLD_MINUTES = 3
MAX_ATTEMPTS = 5


class Command(BaseCommand):
    help = "Detect stuck RUNNING jobs (no heartbeat) → RETRY_WAIT or DEAD"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only log what would be done",
        )
        parser.add_argument(
            "--threshold",
            type=int,
            default=STUCK_THRESHOLD_MINUTES,
            help=f"Minutes without heartbeat to consider stuck (default: {STUCK_THRESHOLD_MINUTES})",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        threshold_minutes = options.get("threshold", STUCK_THRESHOLD_MINUTES)
        cutoff = timezone.now() - timedelta(minutes=threshold_minutes)

        qs = VideoTranscodeJob.objects.filter(
            state=VideoTranscodeJob.State.RUNNING,
            last_heartbeat_at__lt=cutoff,
        ).order_by("id")

        recovered = 0
        dead = 0

        for job in qs:
            attempt_after = job.attempt_count + 1
            if attempt_after >= MAX_ATTEMPTS:
                if dry_run:
                    self.stdout.write(
                        f"DRY-RUN DEAD | job_id={job.id} video_id={job.video_id} attempt_count={job.attempt_count}"
                    )
                else:
                    job.state = VideoTranscodeJob.State.DEAD
                    job.error_code = "STUCK_MAX_ATTEMPTS"
                    job.error_message = f"Stuck (no heartbeat for {threshold_minutes}min)"
                    job.locked_by = ""
                    job.locked_until = None
                    job.save(update_fields=["state", "error_code", "error_message", "locked_by", "locked_until", "updated_at"])
                    self.stdout.write(self.style.WARNING(f"DEAD | job_id={job.id} video_id={job.video_id}"))
                dead += 1
            else:
                if dry_run:
                    self.stdout.write(
                        f"DRY-RUN RETRY_WAIT | job_id={job.id} video_id={job.video_id} attempt_count={job.attempt_count}→{attempt_after}"
                    )
                else:
                    job.state = VideoTranscodeJob.State.RETRY_WAIT
                    job.attempt_count = attempt_after
                    job.locked_by = ""
                    job.locked_until = None
                    job.save(update_fields=["state", "attempt_count", "locked_by", "locked_until", "updated_at"])
                    self.stdout.write(
                        self.style.SUCCESS(f"RETRY_WAIT | job_id={job.id} video_id={job.video_id} attempt={attempt_after}")
                    )
                recovered += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done: recovered={recovered} dead={dead}" + (" (dry-run)" if dry_run else ""))
        )
