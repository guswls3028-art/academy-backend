# PATH: apps/support/video/management/commands/expire_playback_sessions.py
"""
Mark expired VideoPlaybackSession rows: ACTIVE with expires_at < now -> EXPIRED.

Run via cron (e.g. daily):
  python manage.py expire_playback_sessions

No Celery. No Redis.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from academy.adapters.db.django import repositories_video as video_repo


class Command(BaseCommand):
    help = "Mark ACTIVE playback sessions with expires_at < now as EXPIRED"

    def handle(self, *args, **options):
        now = timezone.now()
        updated = video_repo.playback_session_update_expired(now)
        self.stdout.write(self.style.SUCCESS(f"Marked {updated} session(s) as EXPIRED"))
