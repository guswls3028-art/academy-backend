# PATH: apps/support/video/management/commands/reconcile_playback_sessions.py

from django.core.management.base import BaseCommand
from django.utils import timezone

from libs.redis_client.client import redis_client

from apps.support.video.models import VideoPlaybackSession


def _key_user_sessions(user_id: int) -> str:
    return f"media:playback:user:{int(user_id)}:sessions"


def _key_user_revoked(user_id: int) -> str:
    return f"media:playback:user:{int(user_id)}:revoked"


class Command(BaseCommand):
    help = "Reconcile VideoPlaybackSession DB state with Redis (EXPIRED/REVOKED)"

    def handle(self, *args, **options):
        now = timezone.now()

        qs = VideoPlaybackSession.objects.filter(
            status=VideoPlaybackSession.Status.ACTIVE
        ).select_related("enrollment", "enrollment__student")

        expired = 0
        revoked = 0

        for s in qs.iterator():
            user_id = s.enrollment.student_id
            session_id = s.session_id

            # revoked wins
            if redis_client.sismember(_key_user_revoked(user_id), session_id):
                VideoPlaybackSession.objects.filter(id=s.id).update(
                    status=VideoPlaybackSession.Status.REVOKED,
                    ended_at=now,
                )
                revoked += 1
                continue

            # expired if missing in zset or score <= now
            score = redis_client.zscore(_key_user_sessions(user_id), session_id)
            if score is None or int(score) <= int(now.timestamp()):
                VideoPlaybackSession.objects.filter(id=s.id).update(
                    status=VideoPlaybackSession.Status.EXPIRED,
                    ended_at=now,
                )
                expired += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"reconcile done expired={expired} revoked={revoked}"
            )
        )
