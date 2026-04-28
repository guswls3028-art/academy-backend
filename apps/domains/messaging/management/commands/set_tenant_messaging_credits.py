# apps/support/messaging/management/commands/set_tenant_messaging_credits.py
"""
테넌트 메시징 잔액(credit_balance) · 활성화(messaging_is_active) 세팅.
충전 API를 타지 않고 DB에 직접 반영할 때 사용 (복구/테스트용).

사용:
  python manage.py set_tenant_messaging_credits hakwonplus --balance=50000
  python manage.py set_tenant_messaging_credits hakwonplus --balance=50000 --active
  python manage.py set_tenant_messaging_credits 1 --balance=100000  # tenant id
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.core.models import Tenant


def _get_tenant(identifier: str):
    """code 또는 id로 Tenant 반환."""
    if identifier.isdigit():
        return Tenant.objects.filter(pk=int(identifier)).first()
    return Tenant.objects.filter(code=identifier).first()


class Command(BaseCommand):
    help = "Set tenant credit_balance and/or messaging_is_active (recovery/test)."

    def add_arguments(self, parser):
        parser.add_argument(
            "tenant",
            type=str,
            help="Tenant code or id (e.g. hakwonplus or 1)",
        )
        parser.add_argument(
            "--balance",
            type=str,
            default=None,
            metavar="AMOUNT",
            help="Set credit_balance (won). Example: 50000",
        )
        parser.add_argument(
            "--active",
            action="store_true",
            help="Set messaging_is_active=True",
        )

    def handle(self, *args, **options):
        tenant = _get_tenant(options["tenant"])
        if not tenant:
            self.stderr.write(self.style.ERROR(f"Tenant '{options['tenant']}' not found."))
            return

        updates = []
        if options.get("balance") is not None:
            try:
                amt = Decimal(str(options["balance"]))
                if amt < 0:
                    raise ValueError("balance must be >= 0")
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Invalid --balance: {e}"))
                return
            tenant.credit_balance = amt
            updates.append("credit_balance")

        if options.get("active"):
            tenant.messaging_is_active = True
            updates.append("messaging_is_active")

        if not updates:
            self.stdout.write(
                self.style.WARNING("No changes. Use --balance=50000 and/or --active.")
            )
            self._print_current(tenant)
            return

        tenant.save(update_fields=updates)
        self.stdout.write(
            self.style.SUCCESS(f"Updated tenant id={tenant.id} code={tenant.code}: {', '.join(updates)}")
        )
        self._print_current(tenant)

    def _print_current(self, tenant):
        tenant.refresh_from_db()
        self.stdout.write(
            f"  credit_balance={tenant.credit_balance} messaging_is_active={tenant.messaging_is_active}"
        )
