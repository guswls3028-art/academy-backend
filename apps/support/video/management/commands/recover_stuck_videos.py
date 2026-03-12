"""
Recover videos stuck in PENDING or FAILED with no active job.

Handles two scenarios:
1. PENDING + file_key + stale >1h → transition to UPLOADED + enqueue job
2. PENDING + no file_key + stale >24h → mark FAILED (upload never completed)

Run via cron (e.g. every 30 min):
  python manage.py recover_stuck_videos
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.support.video.models import Video, VideoTranscodeJob


class Command(BaseCommand):
    help = "Recover stuck PENDING videos (stale uploads, abandoned uploads)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only log what would be done, do not modify",
        )
        parser.add_argument(
            "--pending-with-file-hours",
            type=int,
            default=1,
            help="Hours before PENDING+file_key is considered stuck (default: 1)",
        )
        parser.add_argument(
            "--pending-no-file-hours",
            type=int,
            default=24,
            help="Hours before PENDING without file_key is marked FAILED (default: 24)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Max videos to process per run (default: 50)",
        )

    def handle(self, *args, **options):
        from apps.support.video.services.video_encoding import create_job_and_submit_batch

        dry_run = options["dry_run"]
        pending_file_hours = options["pending_with_file_hours"]
        pending_nofile_hours = options["pending_no_file_hours"]
        limit = options["limit"]

        now = timezone.now()

        # Exclude videos that have an active job (QUEUED/RUNNING/RETRY_WAIT)
        active_job_video_ids = VideoTranscodeJob.objects.filter(
            state__in=[
                VideoTranscodeJob.State.QUEUED,
                VideoTranscodeJob.State.RUNNING,
                VideoTranscodeJob.State.RETRY_WAIT,
            ],
        ).values_list("video_id", flat=True)

        recovered = 0
        failed = 0

        # ── Case 1: PENDING + file_key + stale > N hours → UPLOADED + enqueue ──
        pending_with_file_cutoff = now - timedelta(hours=pending_file_hours)
        pending_with_file = (
            Video.objects
            .filter(
                status=Video.Status.PENDING,
                updated_at__lt=pending_with_file_cutoff,
            )
            .exclude(pk__in=active_job_video_ids)
            .filter(file_key__isnull=False)
            .exclude(file_key="")
            .select_related("session__lecture__tenant")
            .order_by("updated_at")[:limit]
        )

        for video in pending_with_file:
            tenant_id = None
            try:
                tenant_id = video.session.lecture.tenant_id
            except (AttributeError, TypeError):
                self.stderr.write(f"SKIP | video_id={video.id} (no tenant)")
                continue

            if dry_run:
                self.stdout.write(
                    f"DRY-RUN RECOVER | video_id={video.id} tenant_id={tenant_id} "
                    f"status=PENDING→UPLOADED age={now - video.updated_at}"
                )
                recovered += 1
                continue

            with transaction.atomic():
                v = Video.objects.select_for_update().filter(pk=video.id).first()
                if not v or v.status != Video.Status.PENDING:
                    continue
                v.status = Video.Status.UPLOADED
                v.save(update_fields=["status", "updated_at"])

            result = create_job_and_submit_batch(v)
            if result.job:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"RECOVERED | video_id={video.id} tenant_id={tenant_id} "
                        f"job_id={result.job.id}"
                    )
                )
            else:
                self.stdout.write(
                    f"RECOVERED (UPLOADED, deferred) | video_id={video.id} "
                    f"tenant_id={tenant_id} reason={result.reject_reason}"
                )
            recovered += 1

        # ── Case 2: PENDING + no file_key + stale > N hours → FAILED ──
        pending_nofile_cutoff = now - timedelta(hours=pending_nofile_hours)
        pending_no_file = (
            Video.objects
            .filter(
                status=Video.Status.PENDING,
                updated_at__lt=pending_nofile_cutoff,
            )
            .exclude(pk__in=active_job_video_ids)
            .filter(Q(file_key__isnull=True) | Q(file_key=""))
            .order_by("updated_at")[:limit]
        )

        for video in pending_no_file:
            if dry_run:
                self.stdout.write(
                    f"DRY-RUN FAIL | video_id={video.id} "
                    f"status=PENDING→FAILED (no file_key, age={now - video.updated_at})"
                )
                failed += 1
                continue

            with transaction.atomic():
                v = Video.objects.select_for_update().filter(pk=video.id).first()
                if not v or v.status != Video.Status.PENDING:
                    continue
                v.status = Video.Status.FAILED
                v.error_reason = "Upload abandoned (no file uploaded within timeout)"
                v.save(update_fields=["status", "error_reason", "updated_at"])
            self.stdout.write(
                self.style.WARNING(f"FAILED | video_id={video.id} (abandoned upload)")
            )
            failed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: recovered={recovered} failed={failed}"
                + (" (dry-run)" if dry_run else "")
            )
        )
