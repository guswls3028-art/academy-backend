# PATH: apps/domains/community/management/commands/list_qna_posts.py
"""최근 QnA 게시물 목록 출력 (프로덕션 DB 점검용)."""
from django.core.management.base import BaseCommand
from apps.domains.community.models import PostEntity


class Command(BaseCommand):
    help = "List recent QnA posts (id, tenant_id, title, created_by_id)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20, help="최대 건수")

    def handle(self, *args, **options):
        limit = options["limit"]
        qs = (
            PostEntity.objects.filter(block_type__code__iexact="qna")
            .order_by("-created_at")[:limit]
            .values("id", "tenant_id", "title", "created_by_id", "created_at")
        )
        rows = list(qs)
        for p in rows:
            title = (p["title"] or "")[:50]
            self.stdout.write(
                f"id={p['id']} tenant_id={p['tenant_id']} created_by_id={p['created_by_id']} title={title!r} created_at={p['created_at']}"
            )
        self.stdout.write(self.style.SUCCESS(f"Total: {len(rows)}"))
