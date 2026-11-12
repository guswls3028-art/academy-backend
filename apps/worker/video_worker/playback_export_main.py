"""AWS Batch entry point for an on-demand lesson MP4 export."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from uuid import UUID


logger = logging.getLogger(__name__)


def process_export(export_id: int, attempt: UUID) -> bool:
    if not os.environ.get("AWS_BATCH_JOB_ID"):
        raise RuntimeError("Playback export requires AWS Batch")
    from django.conf import settings
    from django.utils import timezone

    from academy.adapters.storage.r2_objects import video_bucket_client
    from academy.adapters.video.playback_export import build_playback_mp4
    from apps.core.r2_paths import video_hls_master_path, video_playback_export_key
    from apps.domains.video.models import Video, VideoPlaybackExport

    status = VideoPlaybackExport.Status
    now = timezone.now()
    jobs = VideoPlaybackExport.objects.filter(pk=export_id, attempt=attempt)
    if not jobs.filter(status=status.QUEUED, updated_at__gte=now - timedelta(minutes=45)).update(
        status=status.PROCESSING, updated_at=now,
    ):
        return False

    try:
        job = jobs.select_related("video").get()
        video = job.video
        master = video_hls_master_path(video.tenant_id, video.pk)
        if video.source_type != Video.SourceType.UPLOADED or video.status != Video.Status.READY or video.hls_path != master:
            raise ValueError("source_unavailable")
        bucket = settings.R2_VIDEO_BUCKET
        key = video_playback_export_key(video.tenant_id, video.pk, attempt)
        with tempfile.TemporaryDirectory(prefix="video-playback-export-") as temp:
            output = Path(temp) / "video.mp4"
            build_playback_mp4(bucket=bucket, master_key=master, output=output)
            client = video_bucket_client()
            with output.open("rb") as file:
                client.upload_fileobj(file, bucket, key, ExtraArgs={
                    "ContentType": "video/mp4", "CacheControl": "private, no-store",
                })
            ready_at = timezone.now()
            if not jobs.filter(status=status.PROCESSING).update(
                status=status.READY, expires_at=ready_at + timedelta(hours=24), updated_at=ready_at,
            ):
                client.delete_object(Bucket=bucket, Key=key)
                return False
    except Exception as exc:
        logger.warning("VIDEO_PLAYBACK_EXPORT_FAILED export_id=%s error_class=%s", export_id, type(exc).__name__)
        jobs.filter(status=status.PROCESSING).update(status=status.FAILED, updated_at=timezone.now())
        return False
    return True


def main() -> int:
    if len(sys.argv) != 3 or not os.environ.get("DJANGO_SETTINGS_MODULE") or not os.environ.get("AWS_BATCH_JOB_ID"):
        return 2
    export_id, attempt = int(sys.argv[1]), UUID(sys.argv[2])
    import django

    django.setup()
    return 0 if process_export(export_id, attempt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
