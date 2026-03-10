# PATH: apps/support/video/management/commands/enqueue_uploaded_videos.py
"""
Enqueue UPLOADED videos that have no active job.

This picks up videos left in UPLOADED status when create_job_and_submit_batch
was skipped due to tenant/global concurrency limits (e.g. 5 simultaneous uploads
with a tenant limit of 2). Respects existing concurrency limits.

Run via cron or EventBridge (e.g. every 10 min):
  python manage.py enqueue_uploaded_videos
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.support.video.models import Video, VideoTranscodeJob
from apps.support.video.services.video_encoding import create_job_and_submit_batch


class Command(BaseCommand):
    help = "Enqueue UPLOADED videos without active jobs (deferred due to concurrency limits)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only log what would be enqueued, do not create jobs",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Max videos to process per run (default: 20)",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        limit = options.get("limit", 20)

        # Find UPLOADED videos with no active job (QUEUED/RUNNING/RETRY_WAIT)
        active_job_video_ids = VideoTranscodeJob.objects.filter(
            state__in=[
                VideoTranscodeJob.State.QUEUED,
                VideoTranscodeJob.State.RUNNING,
                VideoTranscodeJob.State.RETRY_WAIT,
            ],
        ).values_list("video_id", flat=True)

        candidates = (
            Video.objects.filter(status=Video.Status.UPLOADED)
            .exclude(pk__in=active_job_video_ids)
            .filter(file_key__isnull=False)
            .exclude(file_key="")
            .select_related("session__lecture__tenant")
            .order_by("updated_at")[:limit]
        )

        enqueued = 0
        skipped = 0

        for video in candidates:
            tenant_id = None
            try:
                tenant_id = video.session.lecture.tenant_id
            except (AttributeError, TypeError):
                self.stderr.write(f"SKIP | video_id={video.id} (no tenant)")
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(
                    f"DRY-RUN enqueue | video_id={video.id} tenant_id={tenant_id}"
                )
                enqueued += 1
                continue

            result = create_job_and_submit_batch(video)
            if result.job:
                enqueued += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"ENQUEUED | video_id={video.id} tenant_id={tenant_id} job_id={result.job.id}"
                    )
                )
            else:
                skipped += 1
                self.stdout.write(
                    f"SKIPPED | video_id={video.id} tenant_id={tenant_id} reason={result.reject_reason}"
                )
                # Stop processing if we hit tenant/global limit — no point trying more
                if result.reject_reason in ("tenant_limit", "global_limit"):
                    self.stdout.write(f"Stopping: {result.reject_reason} reached")
                    break

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: enqueued={enqueued} skipped={skipped}"
                + (" (dry-run)" if dry_run else "")
            )
        )
