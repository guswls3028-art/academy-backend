"""Publish the four owner-supplied launch links without changing existing content."""
import uuid
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.landing_public.models import PublicResourceBoardAccess, PublicVideo, PublicVideoEvent, PublicVideoShowcase
from apps.domains.landing_public.services.publishing import can_publish, require_publisher
from apps.domains.landing_public.services.youtube import resolve_youtube

LAUNCH_VIDEOS = (
    ("xgjL2HyrdFM", "통합과학 잘하는 학생들의 공통점", "", "interview", "landscape"),
    ("YYTULV4h4Io", "AI가 스타강사를 대체할까요?", "", "interview", "landscape"),
    ("Q70aZV1TH1Y", "통합과학, 이해일까 암기일까?", "", "lesson", "vertical"),
    ("5EX6-IKy5dc", "신민쌤의 밸런스 게임", "", "news", "vertical"),
)


class Command(BaseCommand):
    help = "Plan (default) or publish only missing godmin launch videos. Existing drafts, publication and hero are preserved."

    def add_arguments(self, parser):
        parser.add_argument("--actor-id", type=int, required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(code="godmin", is_active=True).first()
        access = PublicResourceBoardAccess.objects.filter(tenant=tenant).select_related("publisher_one", "publisher_two").first()
        if not tenant or not access or not access.publisher_one or not access.publisher_two:
            raise CommandError("godmin and its exact two publishers must already be configured.")
        publishers = (access.publisher_one, access.publisher_two)
        if any(not can_publish(SimpleNamespace(user=user, tenant=tenant)) for user in publishers):
            raise CommandError("Both designated publishers must have active tenant publishing access.")
        actor = next((user for user in publishers if user.pk == options["actor_id"]), None)
        if not actor:
            raise CommandError("actor-id must match a designated godmin publisher.")
        existing = set(PublicVideo.objects.filter(tenant=tenant).values_list("youtube_video_id", flat=True))
        self.stdout.write(f"tenant_id={tenant.pk}; publisher_ids={access.publisher_one_id},{access.publisher_two_id}; actor_id={actor.pk}")
        for item in LAUNCH_VIDEOS:
            self.stdout.write(f"{item[0]}: {'preserve' if item[0] in existing else 'publish missing'}")
        if not options["apply"]:
            self.stdout.write("PLAN ONLY: no writes. Pass --apply after reviewing this exact tenant/publisher plan.")
            return
        # All external lookups precede the transaction; an unavailable source aborts all writes.
        sources = {item[0]: resolve_youtube(f"https://www.youtube.com/watch?v={item[0]}")
                   for item in LAUNCH_VIDEOS if item[0] not in existing}
        request = SimpleNamespace(user=actor, tenant=tenant)
        created_ids = []
        with transaction.atomic():
            require_publisher(request, lock=True)
            for youtube_id, title, summary, category, video_format in LAUNCH_VIDEOS:
                if PublicVideo.objects.filter(tenant=tenant, youtube_video_id=youtube_id).exists():
                    continue
                source = sources[youtube_id]
                content = {"title": title, "summary": summary, "category": category, "format": video_format}
                row = PublicVideo.objects.create(
                    tenant=tenant, youtube_video_id=youtube_id, canonical_url=source["canonical_url"],
                    source_title=source["source_title"], channel_title=source["channel_title"], thumbnail_url=source["thumbnail_url"],
                    metadata_checked_at=timezone.now(), draft_content=content, published_content=content,
                    creation_content=content, request_id=uuid.uuid4(), status="published", published_at=timezone.now(),
                    created_by=actor, updated_by=actor,
                )
                created_ids.append(str(row.id))
                PublicVideoEvent.objects.create(tenant=tenant, video=row, actor=actor, action="launch_publish", video_revision=row.revision)
            hero = PublicVideo.objects.filter(tenant=tenant, youtube_video_id=LAUNCH_VIDEOS[0][0], status="published").first()
            # An existing empty showcase can represent a deliberate editorial choice.
            showcase, created = PublicVideoShowcase.objects.get_or_create(tenant=tenant, defaults={"hero_video": hero, "revision": 1 if hero else 0})
            if created and hero:
                PublicVideoEvent.objects.create(tenant=tenant, video=hero, actor=actor, action="hero", video_revision=hero.revision, detail={"hero_id": str(hero.id), "previous_id": None})
        self.stdout.write(f"created={len(created_ids)}; ids={','.join(created_ids)}; hero_id={showcase.hero_video_id}")
