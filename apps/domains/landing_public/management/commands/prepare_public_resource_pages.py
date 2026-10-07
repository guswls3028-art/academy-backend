"""Warm derived reader pages for exact existing published posts; preserve originals."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Tenant
from apps.domains.landing_public.models.resource import PublicResourceFile, PublicResourcePost
from apps.domains.landing_public.services.resource_reader import needs_page_images, prepare_reader, reader_state


class Command(BaseCommand):
    help = "Plan native page preparation for exact published posts; enqueue only with --apply."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-code", required=True)
        parser.add_argument("--post-ids", type=int, nargs="+", required=True)
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(code=options["tenant_code"], is_active=True).first()
        ids = set(options["post_ids"])
        if tenant is None or not 0 < len(ids) <= 100:
            raise CommandError("Exact active tenant and 1–100 post IDs are required.")
        posts = PublicResourcePost.objects.select_for_update().filter(tenant=tenant, pk__in=ids, status="published")
        if set(posts.order_by("pk").values_list("pk", flat=True)) != ids:
            raise CommandError("Every requested post must be published in the exact tenant.")
        files = PublicResourceFile.objects.select_for_update().filter(
            tenant=tenant, post_id__in=ids, is_ready=True, is_removed=False,
        ).order_by("post_id", "position", "id")
        candidates = [file for file in files if needs_page_images(file)]
        for file in candidates:
            status = reader_state(prepare_reader(file)) if options["apply"] else reader_state(file)
            self.stdout.write(f"post={file.post_id} file={file.pk} original_bytes={file.size} status={status}")
        self.stdout.write(f"tenant={tenant.pk} posts={len(ids)} files={len(candidates)} applied={bool(options['apply'])}")
