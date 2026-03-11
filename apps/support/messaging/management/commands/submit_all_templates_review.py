# apps/support/messaging/management/commands/submit_all_templates_review.py
"""
모든 테넌트의 미신청(solapi_status="") 템플릿을 솔라피에 검수 신청.

사용법:
    python manage.py submit_all_templates_review
    python manage.py submit_all_templates_review --tenant=tchul.com
    python manage.py submit_all_templates_review --dry-run
"""
import time
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.models import Tenant
from apps.support.messaging.models import MessageTemplate
from apps.support.messaging.solapi_template_client import (
    create_kakao_template,
    validate_template_variables,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "모든 테넌트의 미신청 템플릿을 솔라피에 일괄 검수 신청"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=str,
            default=None,
            help="특정 테넌트만 처리 (테넌트 code 또는 ID)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="실제 API 호출 없이 대상만 출력",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        tenant_filter = options["tenant"]

        # 시스템 기본 솔라피 키
        default_api_key = getattr(settings, "SOLAPI_API_KEY", "") or ""
        default_api_secret = getattr(settings, "SOLAPI_API_SECRET", "") or ""

        # 대상 테넌트 조회
        tenants_qs = Tenant.objects.all()
        if tenant_filter:
            if tenant_filter.isdigit():
                tenants_qs = tenants_qs.filter(id=int(tenant_filter))
            else:
                tenants_qs = tenants_qs.filter(code=tenant_filter)

        submitted = 0
        skipped = 0
        failed = 0

        for tenant in tenants_qs:
            pfid = (tenant.kakao_pfid or "").strip()
            if not pfid:
                self.stdout.write(f"  [{tenant.code}] PFID 없음 → 건너뜀")
                continue

            # API 키: 테넌트 자체 키 우선, 없으면 시스템 기본
            api_key = (tenant.own_solapi_api_key or "").strip() or default_api_key
            api_secret = (tenant.own_solapi_api_secret or "").strip() or default_api_secret
            if not api_key or not api_secret:
                self.stdout.write(f"  [{tenant.code}] API 키 없음 → 건너뜀")
                continue

            # 미신청 템플릿만
            templates = MessageTemplate.objects.filter(
                tenant=tenant,
                solapi_status="",
            )

            if not templates.exists():
                self.stdout.write(f"  [{tenant.code}] 미신청 템플릿 없음")
                continue

            self.stdout.write(
                self.style.SUCCESS(f"\n▶ [{tenant.code}] 미신청 템플릿 {templates.count()}개")
            )

            for t in templates:
                # 변수 검증
                ok, errs = validate_template_variables(t.body, t.subject or "")
                if not ok:
                    self.stdout.write(
                        self.style.WARNING(f"    ✗ {t.name} — 변수 오류: {'; '.join(errs)}")
                    )
                    skipped += 1
                    continue

                content = (
                    (t.subject.strip() + "\n" + t.body).strip()
                    if t.subject
                    else t.body
                )

                if dry_run:
                    self.stdout.write(f"    [DRY] {t.name} (id={t.id})")
                    submitted += 1
                    continue

                try:
                    result = create_kakao_template(
                        api_key=api_key,
                        api_secret=api_secret,
                        channel_id=pfid,
                        name=t.name,
                        content=content,
                        category_code="TE",
                    )
                    template_id = result.get("templateId", "")
                    t.solapi_template_id = template_id
                    t.solapi_status = "PENDING"
                    t.save(update_fields=["solapi_template_id", "solapi_status", "updated_at"])
                    self.stdout.write(
                        self.style.SUCCESS(f"    ✓ {t.name} → {template_id}")
                    )
                    submitted += 1
                    # 솔라피 API rate limit 방지
                    time.sleep(0.5)
                except ValueError as e:
                    self.stdout.write(
                        self.style.ERROR(f"    ✗ {t.name} — {e}")
                    )
                    failed += 1
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"    ✗ {t.name} — 예외: {e}")
                    )
                    failed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\n완료: 신청 {submitted}건, 건너뜀 {skipped}건, 실패 {failed}건"
                + (" (DRY RUN)" if dry_run else "")
            )
        )
