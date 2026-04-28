# PATH: apps/support/video/management/commands/purge_raw_videos.py
"""
Delete R2 raw files for READY videos older than N days.

After transcoding completes, raw source files are kept for safety.
This command cleans them up after a retention period (default: 3 days).

Run via cron (e.g. daily):
  python manage.py purge_raw_videos
  python manage.py purge_raw_videos --retention-days=3 --dry-run
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.domains.video.models import Video

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 3
DEFAULT_BATCH_SIZE = 100


class Command(BaseCommand):
    help = "Delete R2 raw files for READY videos older than retention period (default 3 days)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-days",
            type=int,
            default=DEFAULT_RETENTION_DAYS,
            help=f"Days after READY before deleting raw file (default: {DEFAULT_RETENTION_DAYS})",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help=f"Max videos per run (default: {DEFAULT_BATCH_SIZE})",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List files that would be deleted without actually deleting them",
        )

    def handle(self, *args, **options):
        retention_days = options["retention_days"]
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        cutoff = timezone.now() - timedelta(days=retention_days)

        # READY videos with raw file_key, updated before cutoff
        candidates = (
            Video.objects.filter(
                status=Video.Status.READY,
                file_key__isnull=False,
                updated_at__lt=cutoff,
            )
            .exclude(file_key="")
            .order_by("updated_at")[:batch_size]
        )

        deleted = 0
        failed = 0

        for video in candidates:
            file_key = video.file_key
            if dry_run:
                self.stdout.write(
                    f"DRY-RUN delete raw | video_id={video.id} key={file_key[:80]}"
                )
                deleted += 1
                continue

            try:
                from apps.infrastructure.storage.r2 import delete_object_r2_video
                delete_object_r2_video(key=file_key)
            except Exception as e:
                logger.warning("R2 raw delete failed video_id=%s: %s", video.id, e)
                failed += 1
                continue

            video.file_key = ""
            video.save(update_fields=["file_key", "updated_at"])
            deleted += 1
            logger.info("R2 raw deleted: video_id=%s key=%s", video.id, file_key[:80])

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: deleted={deleted} failed={failed}"
                + (" (dry-run)" if dry_run else "")
            )
        )
