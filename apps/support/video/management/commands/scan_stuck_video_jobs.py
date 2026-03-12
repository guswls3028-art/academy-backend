# PATH: apps/support/video/management/commands/scan_stuck_video_jobs.py
"""
Stuck Scanner: RUNNING인데 last_heartbeat_at 기준 heartbeat_age가 threshold 초과 → RETRY_WAIT, attempt_count++.
Stuck 판정은 실행시간이 아니라 heartbeat_age 기반만 사용. standard 20분, long(3h+ 영상) 45분.

attempt_count >= MAX 이면 DEAD 처리 (job_mark_dead 사용 → Video.status FAILED 반영).
RETRY_WAIT 전환 시 submit_batch_job 호출 (Batch 재제출, duration 전달로 동일 tier 유지).

Run via cron (e.g. every 2 min):
  python manage.py scan_stuck_video_jobs
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from datetime import timedelta

from apps.support.video.models import VideoTranscodeJob
from apps.support.video.services.batch_submit import submit_batch_job


STUCK_THRESHOLD_MINUTES = 3
MAX_ATTEMPTS = 5


def _stuck_threshold_minutes(job: VideoTranscodeJob) -> int:
    """heartbeat_age 기준 stuck threshold. 3시간 이상 영상이면 long(45분), 아니면 standard(20분)."""
    standard = int(getattr(settings, "VIDEO_STUCK_HEARTBEAT_STANDARD_MINUTES", 20))
    long_min = int(getattr(settings, "VIDEO_STUCK_HEARTBEAT_LONG_MINUTES", 45))
    threshold_sec = int(getattr(settings, "VIDEO_LONG_DURATION_THRESHOLD_SECONDS", 10800))
    try:
        dur = getattr(job.video, "duration", None) or 0
        if dur and int(dur) >= threshold_sec:
            return long_min
    except Exception:
        pass
    return standard


class Command(BaseCommand):
    help = "Detect stuck RUNNING jobs (heartbeat_age only) → RETRY_WAIT or DEAD"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only log what would be done",
        )
        parser.add_argument(
            "--threshold",
            type=int,
            default=None,
            help="Override minutes without heartbeat (default: per-job standard/long from settings)",
        )

    def handle(self, *args, **options):
        from academy.adapters.db.django.repositories_video import job_mark_dead

        dry_run = options.get("dry_run", False)
        override_threshold = options.get("threshold")

        qs = VideoTranscodeJob.objects.filter(
            state=VideoTranscodeJob.State.RUNNING,
        ).select_related("video").order_by("id")

        recovered = 0
        dead = 0

        for job in qs:
            threshold_minutes = override_threshold if override_threshold is not None else _stuck_threshold_minutes(job)
            cutoff = timezone.now() - timedelta(minutes=threshold_minutes)
            if job.last_heartbeat_at >= cutoff:
                continue

            attempt_after = job.attempt_count + 1
            if attempt_after >= MAX_ATTEMPTS:
                if dry_run:
                    self.stdout.write(
                        f"DRY-RUN DEAD | job_id={job.id} video_id={job.video_id} attempt_count={job.attempt_count}"
                    )
                else:
                    job_mark_dead(
                        str(job.id),
                        error_code="STUCK_MAX_ATTEMPTS",
                        error_message=f"Stuck (heartbeat_age > {threshold_minutes}min)",
                    )
                    self.stdout.write(self.style.WARNING(f"DEAD | job_id={job.id} video_id={job.video_id}"))
                dead += 1
            else:
                duration_sec = None
                try:
                    if job.video and getattr(job.video, "duration", None):
                        duration_sec = int(job.video.duration)
                except Exception:
                    pass
                if dry_run:
                    self.stdout.write(
                        f"DRY-RUN RETRY_WAIT | job_id={job.id} video_id={job.video_id} attempt_count={job.attempt_count}→{attempt_after} threshold={threshold_minutes}min"
                    )
                else:
                    job.state = VideoTranscodeJob.State.RETRY_WAIT
                    job.attempt_count = attempt_after
                    job.locked_by = ""
                    job.locked_until = None
                    job.save(update_fields=["state", "attempt_count", "locked_by", "locked_until", "updated_at"])

                    # In daemon mode, short videos are picked up by daemon polling (no Batch submit needed).
                    # Only submit to Batch for long videos or when in batch mode.
                    worker_mode = getattr(settings, "VIDEO_WORKER_MODE", "batch")
                    daemon_max = int(getattr(settings, "DAEMON_MAX_DURATION_SECONDS", 1800))
                    use_batch = (worker_mode != "daemon") or (duration_sec and duration_sec > daemon_max)

                    if use_batch:
                        aws_job_id, submit_err = submit_batch_job(str(job.id), duration_seconds=duration_sec)
                        if aws_job_id:
                            job.aws_batch_job_id = aws_job_id
                            job.save(update_fields=["aws_batch_job_id", "updated_at"])
                            self.stdout.write(
                                self.style.SUCCESS(f"RETRY_WAIT + BATCH_SUBMIT | job_id={job.id} video_id={job.video_id} attempt={attempt_after}")
                            )
                        else:
                            job.error_code = "BATCH_SUBMIT_FAILED"
                            job.error_message = (submit_err or "submit failed")[:2000]
                            job.save(update_fields=["error_code", "error_message", "updated_at"])
                            self.stderr.write(f"RETRY_WAIT (batch submit failed) | job_id={job.id} video_id={job.video_id}")
                    else:
                        self.stdout.write(
                            self.style.SUCCESS(f"RETRY_WAIT (daemon will poll) | job_id={job.id} video_id={job.video_id} attempt={attempt_after}")
                        )
                recovered += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done: recovered={recovered} dead={dead}" + (" (dry-run)" if dry_run else ""))
        )
