"""
구독 상태 정합성 안전망.

process_billing 실패/누락 시에도 운영 정합성을 회복한다.
매일 실행 권장 (EventBridge 06:00 KST).

점검 항목:
1. active인데 expires_at가 이미 지난 경우 → expired
2. grace인데 유예 기간(7일)도 지난 경우 → expired
3. cancel_at_period_end=True이고 만료일 지난 경우 → expired
4. expired인데 유효한 PAID 인보이스가 있는 경우 → active로 복원
5. exempt 테넌트 점검

Usage:
  python manage.py sync_subscription
  python manage.py sync_subscription --dry-run
"""

from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.billing.models import Invoice
from apps.billing.services import subscription_service
from apps.core.models.program import Program


class Command(BaseCommand):
    help = "구독 상태 정합성 안전망 — 상태/만료일/인보이스 교차 검증"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="변경 없이 불일치만 보고",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        today = date.today()
        grace_days = settings.BILLING_GRACE_PERIOD_DAYS
        exempt = settings.BILLING_EXEMPT_TENANT_IDS
        fixes = []

        programs = Program.objects.select_related("tenant").exclude(
            tenant_id__in=exempt
        )

        for program in programs:
            fix = self._check_program(program, today, grace_days, dry_run)
            if fix:
                fixes.append(fix)

        if fixes:
            self.stdout.write(self.style.WARNING(f"\n{'[DRY RUN] ' if dry_run else ''}정합성 수정 {len(fixes)}건:"))
            for f in fixes:
                self.stdout.write(f"  {f}")
        else:
            self.stdout.write(self.style.SUCCESS("[OK] 모든 구독 상태 정합성 OK"))

    def _check_program(self, program: Program, today: date, grace_days: int, dry_run: bool) -> str | None:
        tenant_code = program.tenant.code
        expires = program.subscription_expires_at
        status = program.subscription_status

        # 1. active인데 만료일 지남
        if status == "active" and expires and expires < today:
            if program.cancel_at_period_end:
                # 해지 예약 + 만료 → expired
                msg = f"{tenant_code}: active+cancel_at_period_end, expired {expires} → expired"
                if not dry_run:
                    subscription_service.expire(program.pk)
                return msg
            else:
                # 결제 실패 없이 만료? grace 진입
                msg = f"{tenant_code}: active, expired {expires} → grace"
                if not dry_run:
                    subscription_service.enter_grace(program.pk)
                return msg

        # 2. grace인데 유예 기간도 지남
        if status == "grace" and expires:
            grace_end = expires + timedelta(days=grace_days)
            if today > grace_end:
                msg = f"{tenant_code}: grace, grace_end {grace_end} passed → expired"
                if not dry_run:
                    subscription_service.expire(program.pk)
                return msg

        # 3. expired인데 최근 PAID 인보이스가 있는 경우 → 복원
        if status == "expired":
            latest_paid = Invoice.objects.filter(
                tenant_id=program.tenant_id,
                status="PAID",
                period_end__gte=today,
            ).order_by("-period_end").first()

            if latest_paid:
                msg = (
                    f"{tenant_code}: expired but has PAID invoice "
                    f"{latest_paid.invoice_number} (period_end={latest_paid.period_end}) → active"
                )
                if not dry_run:
                    subscription_service.renew(
                        program.pk,
                        new_expires_at=latest_paid.period_end,
                        next_billing_at=latest_paid.period_end,
                    )
                return msg

        return None
