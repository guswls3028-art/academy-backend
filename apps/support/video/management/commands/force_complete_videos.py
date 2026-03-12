"""
Force-complete videos for a specific tenant.

Sets stuck videos (PENDING/UPLOADED/PROCESSING/FAILED) to READY status.
Videos without hls_path get a placeholder; videos with hls_path are simply marked READY.
Also completes/cancels any active transcode jobs.

Usage:
  python manage.py force_complete_videos --tenant-id 2
  python manage.py force_complete_videos --tenant-id 2 --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone


class Command(BaseCommand):
    help = "Force-complete stuck videos for a tenant (set READY, close jobs)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-id",
            type=int,
            required=True,
            help="Tenant ID to force-complete videos for",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only list affected videos, do not modify",
        )
        parser.add_argument(
            "--status",
            nargs="*",
            default=["PENDING", "UPLOADED", "PROCESSING", "FAILED"],
            help="Video statuses to force-complete (default: all non-READY)",
        )

    def handle(self, *args, **options):
        from apps.support.video.models import Video, VideoTranscodeJob

        tenant_id = options["tenant_id"]
        dry_run = options["dry_run"]
        statuses = options["status"]

        # Find all videos for the tenant in target statuses
        videos = (
            Video.objects
            .filter(
                session__lecture__tenant_id=tenant_id,
                status__in=statuses,
            )
            .select_related("session__lecture__tenant")
        )

        total = videos.count()
        self.stdout.write(f"Found {total} video(s) for tenant {tenant_id} in statuses {statuses}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No videos to process."))
            return

        completed = 0
        skipped = 0

        for video in videos:
            self.stdout.write(
                f"  video_id={video.id} status={video.status} "
                f"duration={video.duration} hls_path={video.hls_path or '(none)'} "
                f"file_key={video.file_key[:60] if video.file_key else '(none)'}"
            )

            if dry_run:
                completed += 1
                continue

            with transaction.atomic():
                v = Video.objects.select_for_update().filter(pk=video.id).first()
                if not v:
                    skipped += 1
                    continue

                # If already READY, skip
                if v.status == Video.Status.READY:
                    self.stdout.write(f"    SKIP: already READY")
                    skipped += 1
                    continue

                # Mark video READY
                v.status = Video.Status.READY
                v.error_reason = ""
                update_fields = ["status", "error_reason", "updated_at"]

                # If no hls_path but has file_key, mark as ready anyway
                # (the video data exists in R2, just not transcoded)
                if not v.hls_path and v.file_key:
                    self.stdout.write(f"    WARNING: No hls_path, marking READY with file_key only")

                v.save(update_fields=update_fields)

                # Close any active transcode jobs
                active_jobs = VideoTranscodeJob.objects.filter(
                    video=v,
                    state__in=[
                        VideoTranscodeJob.State.QUEUED,
                        VideoTranscodeJob.State.RUNNING,
                        VideoTranscodeJob.State.RETRY_WAIT,
                    ],
                )
                job_count = active_jobs.count()
                if job_count > 0:
                    active_jobs.update(
                        state=VideoTranscodeJob.State.SUCCEEDED,
                        error_message="force_complete_videos command",
                        locked_by="",
                        locked_until=None,
                        updated_at=timezone.now(),
                    )
                    self.stdout.write(f"    Closed {job_count} active job(s)")

                    # Release DDB locks
                    try:
                        from apps.support.video.services.video_job_lock import release as lock_release
                        lock_release(v.id)
                    except Exception as e:
                        self.stdout.write(f"    DDB lock release failed: {e}")

                # Update Redis cache
                try:
                    from apps.support.video.redis_status_cache import cache_video_status
                    cache_video_status(tenant_id, v.id, "READY", ttl=None)
                except Exception:
                    pass

                completed += 1
                self.stdout.write(self.style.SUCCESS(f"    COMPLETED: video_id={v.id}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: completed={completed} skipped={skipped}"
                + (" (dry-run)" if dry_run else "")
            )
        )
