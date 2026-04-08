"""
수동 입금 확인.

Usage:
  python manage.py mark_invoice_paid --invoice INV-202604-hakwonplus-001 --dry-run
  python manage.py mark_invoice_paid --invoice INV-202604-limglish-001 --confirm-live
  python manage.py mark_invoice_paid --invoice-id 42 --confirm-live

안전장치:
  - 운영 테넌트는 --confirm-live 필수.
  - --dry-run으로 변경 내용 미리 확인 가능.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.billing.models import Invoice
from apps.billing.services import invoice_service


class Command(BaseCommand):
    help = "Manual payment confirmation -> Invoice PAID + subscription renewal"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--invoice", help="Invoice number (e.g. INV-202604-hakwonplus-001)")
        group.add_argument("--invoice-id", type=int, help="Invoice PK")
        parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
        parser.add_argument("--confirm-live", action="store_true",
                            help="Required for non-exempt (live) tenants")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        confirm_live = options["confirm_live"]

        if options["invoice_id"]:
            try:
                inv = Invoice.objects.select_related("tenant", "tenant__program").get(
                    pk=options["invoice_id"]
                )
            except Invoice.DoesNotExist:
                raise CommandError(f"Invoice ID {options['invoice_id']} not found.")
        else:
            try:
                inv = Invoice.objects.select_related("tenant", "tenant__program").get(
                    invoice_number=options["invoice"]
                )
            except Invoice.DoesNotExist:
                raise CommandError(f"Invoice '{options['invoice']}' not found.")

        tenant = inv.tenant
        is_exempt = tenant.id in settings.BILLING_EXEMPT_TENANT_IDS

        if not is_exempt and not confirm_live and not dry_run:
            raise CommandError(
                f"Tenant '{tenant.code}' is LIVE. "
                f"Use --confirm-live to proceed, or --dry-run to preview."
            )

        program = getattr(tenant, "program", None)

        self._log(f"--- mark_invoice_paid {'[DRY RUN]' if dry_run else ''} ---")
        self._log(f"  Invoice:     {inv.invoice_number} (id={inv.pk})")
        self._log(f"  Tenant:      {tenant.code} (id={tenant.id}){' [EXEMPT]' if is_exempt else ' [LIVE]'}")
        self._log(f"  Plan:        {inv.plan}  Mode: {inv.billing_mode}")
        self._log(f"  Amount:      {inv.total_amount:,}won (supply={inv.supply_amount:,} + tax={inv.tax_amount:,})")
        self._log(f"  Period:      {inv.period_start} ~ {inv.period_end}")
        self._log(f"  Status:      {inv.status}")
        if program:
            self._log(f"  Sub status:  {program.subscription_status}  expires={program.subscription_expires_at}")

        if inv.status == "PAID":
            self._log(f"  Already PAID (paid_at: {inv.paid_at}). No action needed.")
            return

        if inv.status not in ("PENDING", "OVERDUE", "FAILED"):
            raise CommandError(
                f"Cannot mark paid from status '{inv.status}'. "
                f"Only PENDING/OVERDUE/FAILED allowed."
            )

        self._log(f"  AFTER (est): Invoice -> PAID, Sub expires -> {inv.period_end}")

        if dry_run:
            self._log("  [DRY RUN] No changes applied.")
            return

        # FAILED -> PENDING first
        if inv.status == "FAILED":
            invoice_service.retry_pending(inv.pk)

        old_status = inv.status
        inv = invoice_service.mark_paid(inv.pk)

        # Reload program
        if program:
            program.refresh_from_db()

        self._log(f"  AFTER (actual): Invoice status={inv.status}  paid_at={inv.paid_at}")
        if program:
            self._log(f"  AFTER (actual): Sub status={program.subscription_status}  "
                      f"expires={program.subscription_expires_at}  "
                      f"remaining={program.days_remaining} days")
        self.stdout.write(self.style.SUCCESS("  [OK] Payment confirmed."))

    def _log(self, msg):
        try:
            self.stdout.write(msg)
        except UnicodeEncodeError:
            self.stdout.write(msg.encode("ascii", errors="replace").decode("ascii"))
