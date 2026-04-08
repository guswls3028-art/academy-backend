"""
수동 구독 기간 연장.

Usage:
  python manage.py extend_subscription --tenant limglish --days 30 --dry-run
  python manage.py extend_subscription --tenant limglish --days 30 --confirm-live
  python manage.py extend_subscription --tenant hakwonplus --months 1 --confirm-live

안전장치:
  - BILLING_EXEMPT_TENANT_IDS(개발/테스트) 테넌트는 --confirm-live 없이도 실행 가능.
  - 운영 테넌트는 --confirm-live 없으면 실행 거부.
  - --dry-run은 변경 없이 예상 결과만 출력.
"""

from datetime import date

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.billing.services import subscription_service
from apps.core.models.program import Program
from apps.core.models.tenant import Tenant


class Command(BaseCommand):
    help = "Manual subscription period extension"

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Tenant code")
        parser.add_argument("--days", type=int, default=0, help="Days to extend")
        parser.add_argument("--months", type=int, default=0, help="Months to extend")
        parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
        parser.add_argument("--confirm-live", action="store_true",
                            help="Required for non-exempt (live) tenants")

    def handle(self, *args, **options):
        tenant_code = options["tenant"]
        days = options["days"]
        months = options["months"]
        dry_run = options["dry_run"]
        confirm_live = options["confirm_live"]

        if not days and not months:
            raise CommandError("--days or --months required.")

        try:
            tenant = Tenant.objects.get(code=tenant_code)
        except Tenant.DoesNotExist:
            raise CommandError(f"Tenant '{tenant_code}' not found.")

        try:
            program = Program.objects.get(tenant=tenant)
        except Program.DoesNotExist:
            raise CommandError(f"No Program for tenant '{tenant_code}'.")

        is_exempt = tenant.id in settings.BILLING_EXEMPT_TENANT_IDS
        if not is_exempt and not confirm_live and not dry_run:
            raise CommandError(
                f"Tenant '{tenant_code}' is a LIVE tenant. "
                f"Use --confirm-live to proceed, or --dry-run to preview."
            )

        # Calculate total days
        total_days = days
        if months:
            base = program.subscription_expires_at or date.today()
            if base < date.today():
                base = date.today()
            new_date = base + relativedelta(months=months) + relativedelta(days=days)
            total_days = (new_date - base).days
            if total_days <= 0:
                total_days = 30 * months + days

        old_status = program.subscription_status
        old_expires = program.subscription_expires_at

        # Preview
        base_for_preview = old_expires or date.today()
        if base_for_preview < date.today():
            base_for_preview = date.today()
        preview_expires = base_for_preview + relativedelta(days=total_days)

        self._log(f"--- extend_subscription {'[DRY RUN]' if dry_run else ''} ---")
        self._log(f"  Tenant:      {tenant_code} (id={tenant.id}){' [EXEMPT]' if is_exempt else ' [LIVE]'}")
        self._log(f"  Plan:        {program.plan} ({program.monthly_price:,}won)")
        self._log(f"  BEFORE:      status={old_status}  expires={old_expires}")
        self._log(f"  Extension:   +{total_days} days")
        self._log(f"  AFTER (est): status=active  expires={preview_expires}")

        if dry_run:
            self._log("  [DRY RUN] No changes applied.")
            return

        program = subscription_service.extend(program.pk, total_days)

        self._log(f"  AFTER (actual): status={program.subscription_status}  "
                  f"expires={program.subscription_expires_at}  "
                  f"remaining={program.days_remaining} days")
        self.stdout.write(self.style.SUCCESS("  [OK] Extension applied."))

    def _log(self, msg):
        """cp949-safe stdout write."""
        try:
            self.stdout.write(msg)
        except UnicodeEncodeError:
            self.stdout.write(msg.encode("ascii", errors="replace").decode("ascii"))
