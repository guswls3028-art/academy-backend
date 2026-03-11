# apps/support/messaging/management/commands/fix_template_categories.py
"""
기존 기본 템플릿의 카테고리를 default_templates.py 기준으로 수정.

기존에 모두 "default"로 생성된 기본 템플릿을 트리거에 맞는 카테고리로 업데이트.

사용법:
    python manage.py fix_template_categories
    python manage.py fix_template_categories --dry-run
"""
from django.core.management.base import BaseCommand

from apps.support.messaging.models import MessageTemplate, AutoSendConfig
from apps.support.messaging.default_templates import DEFAULT_TEMPLATES


class Command(BaseCommand):
    help = "기존 기본 템플릿의 카테고리를 트리거 기준으로 수정"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="실제 수정 없이 대상만 출력")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        updated = 0

        # AutoSendConfig → trigger → template FK로 연결된 템플릿의 카테고리를 수정
        for trigger, tpl_def in DEFAULT_TEMPLATES.items():
            correct_category = tpl_def["category"]
            configs = AutoSendConfig.objects.filter(
                trigger=trigger,
                template__isnull=False,
            ).select_related("template")

            for config in configs:
                t = config.template
                if t.category != correct_category:
                    self.stdout.write(
                        f"  {t.tenant.code if hasattr(t, 'tenant') else t.tenant_id} / "
                        f"{trigger} / {t.name}: {t.category} → {correct_category}"
                    )
                    if not dry_run:
                        t.category = correct_category
                        t.save(update_fields=["category", "updated_at"])
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\n완료: {updated}건 수정" + (" (DRY RUN)" if dry_run else "")
            )
        )
