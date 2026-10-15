"""Expire provider-derived data without changing publisher-authored videos."""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.domains.landing_public.models.video import PublicVideo


class Command(BaseCommand):
    help = "Clear YouTube statistics older than 28 days, including hidden/deleted videos."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        rows = PublicVideo.objects.filter(youtube_statistics_checked_at__lt=timezone.now() - timedelta(days=28))
        count = rows.count()
        if options["execute"]:
            count = rows.update(youtube_view_count=None, youtube_published_at=None,
                                youtube_statistics_checked_at=None, youtube_statistics_retry_at=None)
        self.stdout.write(json.dumps({"execute": options["execute"], "expired_statistics": count}))
