"""Refresh bounded provider caches outside public HTTP reads."""
import json
import os

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Tenant
from apps.domains.landing_public.models.video import PublicVideo
from apps.domains.landing_public.services.video_statistics import refresh_video_statistics


class Command(BaseCommand):
    help = "Refresh at most 50 due public videos per active tenant; never modify authored content."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--tenant-code")

    def handle(self, *args, **options):
        tenants = Tenant.objects.filter(is_active=True, pk__in=PublicVideo.objects.filter(
            status="published", published_content__isnull=False,
        ).values("tenant_id")).order_by("pk")
        if options["tenant_code"]:
            tenants = tenants.filter(code=options["tenant_code"])
            if not tenants.exists():
                raise CommandError("No active tenant with published videos matches the exact code.")
        if options["execute"] and not os.environ.get("YOUTUBE_DATA_API_KEY", "").strip():
            raise CommandError("YouTube statistics provider is not configured.")
        refreshed = 0
        for tenant in tenants.iterator():
            if options["execute"]:
                refreshed += refresh_video_statistics(tenant)
        self.stdout.write(json.dumps({"execute": options["execute"], "tenants": tenants.count(), "refreshed": refreshed}))
