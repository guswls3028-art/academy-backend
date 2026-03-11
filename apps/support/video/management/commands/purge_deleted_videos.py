# PATH: apps/support/video/management/commands/purge_deleted_videos.py
"""
Permanently delete soft-deleted videos whose retention period has expired.

Default retention: 6 months (--retention-days to override).
Deletes DB rows and R2 storage files (raw + HLS).

Run via cron (e.g. daily):
  python manage.py purge_deleted_videos
  python manage.py purge_deleted_videos --retention-days=180 --batch-size=100 --dry-run

No Celery. No Redis.
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 180  # 6 months
DEFAULT_BATCH_SIZE = 100


class Command(BaseCommand):
    help = "Permanently delete soft-deleted videos past the retention period (default 6 months)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-days",
            type=int,
            default=DEFAULT_RETENTION_DAYS,
            help=f"Days to retain soft-deleted videos before purge (default: {DEFAULT_RETENTION_DAYS})",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help=f"Number of videos to process per batch (default: {DEFAULT_BATCH_SIZE})",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List videos that would be purged without actually deleting them",
        )

    def handle(self, *args, **options):
        retention_days = options["retention_days"]
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        cutoff = timezone.now() - timedelta(days=retention_days)

        from apps.support.video.models import Video
        from apps.core.r2_paths import video_hls_prefix

        # Use all_with_deleted to find soft-deleted videos past retention
        expired_qs = (
            Video.all_with_deleted
            .filter(deleted_at__isnull=False, deleted_at__lt=cutoff)
            .select_related("session__lecture")
            .order_by("deleted_at")
        )

        total = expired_qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("No expired soft-deleted videos to purge."))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(f"[DRY RUN] Would purge {total} video(s):"))
            for v in expired_qs[:50]:
                tenant_id = self._get_tenant_id(v)
                self.stdout.write(f"  id={v.id} tenant={tenant_id} title={v.title!r} deleted_at={v.deleted_at}")
            if total > 50:
                self.stdout.write(f"  ... and {total - 50} more")
            return

        purged = 0
        r2_errors = 0
        offset = 0

        while True:
            # Re-query each batch (rows are deleted between iterations)
            batch = list(
                Video.all_with_deleted
                .filter(deleted_at__isnull=False, deleted_at__lt=cutoff)
                .select_related("session__lecture")
                .order_by("deleted_at")[:batch_size]
            )
            if not batch:
                break

            for video in batch:
                video_id = video.id
                tenant_id = self._get_tenant_id(video)
                file_key = (video.file_key or "").strip()
                hls_prefix = ""

                if tenant_id:
                    hls_prefix = video_hls_prefix(tenant_id=tenant_id, video_id=video_id)

                # Delete R2 storage files
                if file_key or hls_prefix:
                    try:
                        from apps.infrastructure.storage.r2 import (
                            delete_object_r2_video,
                            delete_prefix_r2_video,
                        )

                        if file_key:
                            delete_object_r2_video(key=file_key)
                            logger.info(
                                "purge_deleted_videos: R2 raw deleted video_id=%s key=%s",
                                video_id, file_key[:80],
                            )
                        if hls_prefix:
                            n = delete_prefix_r2_video(prefix=hls_prefix)
                            logger.info(
                                "purge_deleted_videos: R2 HLS deleted video_id=%s prefix=%s count=%s",
                                video_id, hls_prefix, n,
                            )
                    except Exception as e:
                        r2_errors += 1
                        logger.warning(
                            "purge_deleted_videos: R2 delete failed video_id=%s: %s",
                            video_id, e,
                        )
                        # Continue with DB deletion even if R2 fails — orphan R2 objects
                        # are less harmful than retaining expired PII/data in the DB.

                # Hard-delete from DB
                video.hard_delete()
                purged += 1
                logger.info(
                    "purge_deleted_videos: hard-deleted video_id=%s tenant=%s",
                    video_id, tenant_id,
                )

        msg = f"Purged {purged}/{total} expired soft-deleted video(s)."
        if r2_errors:
            msg += f" R2 cleanup errors: {r2_errors} (see logs)."
        self.stdout.write(self.style.SUCCESS(msg))

    @staticmethod
    def _get_tenant_id(video):
        """Extract tenant_id safely from the video's session→lecture chain."""
        try:
            if video.session and video.session.lecture:
                return video.session.lecture.tenant_id
        except Exception:
            pass
        return None
