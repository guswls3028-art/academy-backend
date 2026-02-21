# PATH: apps/support/video/management/commands/reconcile_video_processing.py
"""
Reconcile stuck PROCESSING videos: lease expired or heartbeat missing → reclaim → re-enqueue.

트리거: PROCESSING인데 (leased_until < now 또는 Redis heartbeat 없음)
동작: try_reclaim_video → UPLOADED로 되돌림 → SQS enqueue

Run via cron (e.g. every 5 min):
  python manage.py reconcile_video_processing

FAST_ACK 사용 시 SQS 메시지 유실 복구용.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from academy.adapters.db.django.repositories_video import (
    get_video_queryset_with_relations,
    DjangoVideoRepository,
)
from apps.support.video.models import Video
from apps.support.video.redis_status_cache import has_video_heartbeat
from apps.support.video.services.sqs_queue import VideoSQSQueue


def _tenant_id_from_video(video: Video) -> int | None:
    try:
        return int(video.session.lecture.tenant_id)
    except (AttributeError, TypeError):
        return None


class Command(BaseCommand):
    help = "Reclaim PROCESSING videos (lease expired or no heartbeat) and re-enqueue to SQS"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only log what would be reclaimed, do not reclaim or enqueue",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        repo = DjangoVideoRepository()
        queue = VideoSQSQueue()

        now = timezone.now()
        # PROCESSING 상태인 비디오
        qs = (
            get_video_queryset_with_relations()
            .filter(status=Video.Status.PROCESSING)
            .order_by("id")
        )

        reclaimed = 0
        enqueued = 0

        for video in qs:
            tenant_id = _tenant_id_from_video(video)
            lease_expired = video.leased_until is not None and video.leased_until < now
            no_heartbeat = tenant_id is not None and not has_video_heartbeat(tenant_id, video.id)

            if not lease_expired and not no_heartbeat:
                continue

            prev_leased_by = getattr(video, "leased_by", "") or ""
            prev_leased_until = getattr(video, "leased_until", None)

            force = no_heartbeat
            if dry_run:
                self.stdout.write(
                    f"DRY-RUN reclaim | video_id={video.id} tenant_id={tenant_id} "
                    f"prev_leased_by={prev_leased_by} prev_leased_until={prev_leased_until} "
                    f"lease_expired={lease_expired} no_heartbeat={no_heartbeat} force={force}"
                )
                reclaimed += 1
                continue

            if not repo.try_reclaim_video(video.id, force=force):
                continue

            reclaimed += 1
            self.stdout.write(
                f"RECLAIMED | video_id={video.id} tenant_id={tenant_id} "
                f"prev_leased_by={prev_leased_by} prev_leased_until={prev_leased_until}"
            )

            video.refresh_from_db()
            if video.status != Video.Status.UPLOADED:
                self.stderr.write(f"WARNING: video {video.id} status={video.status} after reclaim")
                continue

            if queue.enqueue(video):
                enqueued += 1
                self.stdout.write(self.style.SUCCESS(f"RE_ENQUEUED | video_id={video.id}"))

        self.stdout.write(
            self.style.SUCCESS(f"Done: reclaimed={reclaimed} enqueued={enqueued}" + (" (dry-run)" if dry_run else ""))
        )
