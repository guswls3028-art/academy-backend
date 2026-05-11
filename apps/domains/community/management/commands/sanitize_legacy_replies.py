"""Legacy reply content sanitize backfill.

2026-05-11 보안 리뷰 L1: sanitize_html이 reply create/update에 무조건 적용된 이후,
그 이전 입력된 reply는 unsanitized HTML 가능성. idempotent backfill 명령.

사용:
    python manage.py sanitize_legacy_replies --dry-run         # preview
    python manage.py sanitize_legacy_replies                   # apply
    python manage.py sanitize_legacy_replies --tenant 1        # 특정 tenant만

idempotent: sanitize_html은 이미 sanitized된 입력에 적용해도 동일 결과.
변경된 row만 update — equality check.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.domains.community.models import PostReply
from apps.domains.community.services.html_sanitizer import sanitize_html


class Command(BaseCommand):
    help = "기존 PostReply.content 재sanitize (보안 리뷰 L1 backfill, idempotent)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="변경 미리보기만, save 안 함")
        parser.add_argument("--tenant", type=int, default=None, help="특정 tenant id만")
        parser.add_argument("--batch", type=int, default=500, help="배치 크기 (default 500)")

    def handle(self, *args, **opts):
        dry = opts.get("dry_run", False)
        tenant_id = opts.get("tenant")
        batch_size = opts.get("batch", 500)

        qs = PostReply.objects.all()
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)

        total = qs.count()
        self.stdout.write(f"전체 reply: {total}건 (tenant_id={tenant_id or 'ALL'})")

        scanned = 0
        changed = 0
        # iterator로 memory 효율적
        for reply in qs.only("id", "content").iterator(chunk_size=batch_size):
            scanned += 1
            old = reply.content or ""
            new = sanitize_html(old)
            if old != new:
                changed += 1
                if not dry:
                    with transaction.atomic():
                        PostReply.objects.filter(id=reply.id).update(content=new)
                self.stdout.write(f"  [{'DRY' if dry else 'FIXED'}] reply#{reply.id} (len {len(old)} → {len(new)})")
            if scanned % 1000 == 0:
                self.stdout.write(f"...{scanned}/{total} (변경 {changed})")

        action = "변경 가능" if dry else "변경됨"
        self.stdout.write(self.style.SUCCESS(f"\n완료 - 전체 {scanned}건 / {action} {changed}건"))
        if dry and changed > 0:
            self.stdout.write("=> 실제 적용: 같은 명령에서 --dry-run 제거")
