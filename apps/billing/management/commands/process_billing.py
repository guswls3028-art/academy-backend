"""
Daily billing batch processor.

Runs at 00:05 KST via EventBridge.

Steps:
1. Create invoices (next_billing_at within 7 days)
2. AUTO_CARD payment attempts (due_date reached)
3. Failed payment retries (next_retry_at reached)
4. Exhausted retries -> OVERDUE
5. Grace period expired -> expired
6. cancel_at_period_end period ended -> expired

Usage:
  python manage.py process_billing
  python manage.py process_billing --dry-run
"""

from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.billing.models import Invoice
from apps.billing.services import invoice_service, payment_service, subscription_service
from apps.core.models.program import Program


class Command(BaseCommand):
    help = "Daily billing batch: invoice creation + payments + state transitions"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        today = date.today()
        exempt = settings.BILLING_EXEMPT_TENANT_IDS
        grace_days = settings.BILLING_GRACE_PERIOD_DAYS
        cutoff = today + timedelta(days=7)

        self._log(f"=== process_billing {today} {'[DRY RUN]' if dry_run else ''} ===")
        self._log(f"  Config: exempt={exempt}, grace_days={grace_days}, "
                  f"auto_billing={'ON' if settings.TOSS_AUTO_BILLING_ENABLED else 'OFF'}")

        # ──── Summary of all non-exempt tenants ────
        all_live = Program.objects.select_related("tenant").exclude(
            tenant_id__in=exempt
        ).order_by("tenant__code")

        self._log(f"\n  Live tenants: {all_live.count()}")
        for p in all_live:
            self._log(f"    {p.tenant.code:15s} status={p.subscription_status:8s} "
                      f"expires={p.subscription_expires_at}  "
                      f"next_bill={p.next_billing_at}  "
                      f"cancel={p.cancel_at_period_end}")

        # ──── 1. Invoice creation (next_billing_at within 7 days) ────
        self._log(f"\n  --- Step 1: Invoice creation (next_billing_at <= {cutoff}) ---")

        upcoming = all_live.filter(
            subscription_status__in=["active", "grace"],
            next_billing_at__lte=cutoff,
        )
        no_billing_date = all_live.filter(
            subscription_status__in=["active", "grace"],
            next_billing_at__isnull=True,
        )

        if no_billing_date.exists():
            self._log(f"    [WARN] {no_billing_date.count()} tenant(s) have next_billing_at=NULL:")
            for p in no_billing_date:
                self._log(f"      {p.tenant.code} (expires={p.subscription_expires_at})")
            self._log(f"    Run: python manage.py audit_billing_fields --fix-next-billing")

        if not upcoming.exists():
            self._log(f"    No tenants with next_billing_at <= {cutoff}. "
                      f"(Earliest: {self._earliest_billing(all_live)})")

        created = 0
        skipped_cancel = 0
        skipped_existing = 0
        for program in upcoming:
            if program.cancel_at_period_end:
                skipped_cancel += 1
                self._log(f"    [SKIP] {program.tenant.code}: cancel_at_period_end=True")
                continue

            existing = Invoice.objects.filter(
                tenant_id=program.tenant_id,
                period_start__gt=program.subscription_expires_at or today,
            ).exclude(status="VOID").exists()
            if existing:
                skipped_existing += 1
                self._log(f"    [SKIP] {program.tenant.code}: invoice already exists for next period")
                continue

            if not dry_run:
                inv = invoice_service.create_for_next_period(program)
                if inv:
                    created += 1
                    self._log(f"    [CREATED] {program.tenant.code}: {inv.invoice_number} "
                              f"period={inv.period_start}~{inv.period_end} amount={inv.total_amount:,}")
            else:
                created += 1
                self._log(f"    [DRY] Would create for {program.tenant.code}")

        self._log(f"    Result: created={created} skipped_cancel={skipped_cancel} "
                  f"skipped_existing={skipped_existing}")

        # ──── 2. AUTO_CARD payment attempts ────
        self._log(f"\n  --- Step 2: AUTO_CARD payment ---")
        # SCHEDULED + PENDING 모두 대상 (PENDING은 이전 배치에서 상태 전이만 된 건)
        due_invoices = Invoice.objects.filter(
            status__in=["SCHEDULED", "PENDING"],
            due_date__lte=today,
            billing_mode="AUTO_CARD",
        ).exclude(tenant_id__in=exempt).select_related("tenant")

        if not due_invoices.exists():
            self._log(f"    No due AUTO_CARD invoices.")
        elif not settings.TOSS_AUTO_BILLING_ENABLED:
            self._log(f"    Due invoices: {due_invoices.count()} -- TOSS_AUTO_BILLING_ENABLED=OFF")
            for inv in due_invoices[:5]:
                self._log(f"      {inv.invoice_number} tenant={inv.tenant.code} "
                          f"amount={inv.total_amount:,} due={inv.due_date}")
        else:
            self._log(f"    Due invoices: {due_invoices.count()} (auto-billing ON)")
            paid_cnt = 0
            failed_cnt = 0
            for inv in due_invoices:
                if dry_run:
                    self._log(f"    [DRY] Would charge {inv.tenant.code}: {inv.invoice_number} "
                              f"amount={inv.total_amount:,}")
                    continue
                result = payment_service.execute_auto_payment(inv.pk)
                if result.get("success"):
                    paid_cnt += 1
                    self._log(f"    [PAID] {inv.tenant.code}: {inv.invoice_number} "
                              f"paymentKey={result.get('payment_key', '')[:16]}...")
                else:
                    failed_cnt += 1
                    self._log(f"    [FAIL] {inv.tenant.code}: {inv.invoice_number} "
                              f"reason={result.get('reason', '')[:80]}")
            self._log(f"    Result: paid={paid_cnt} failed={failed_cnt}")

        # ──── 3. Failed retries ────
        self._log(f"\n  --- Step 3: Failed retries ---")
        retry_invoices = Invoice.objects.filter(
            status="FAILED",
            next_retry_at__lte=today,
            billing_mode="AUTO_CARD",
        ).exclude(tenant_id__in=exempt).select_related("tenant")
        self._log(f"    Retry candidates: {retry_invoices.count()}")

        if retry_invoices.exists() and settings.TOSS_AUTO_BILLING_ENABLED:
            retry_paid = 0
            retry_failed = 0
            for inv in retry_invoices:
                if dry_run:
                    self._log(f"    [DRY] Would retry {inv.tenant.code}: {inv.invoice_number} "
                              f"attempt={inv.attempt_count + 1}")
                    continue
                result = payment_service.execute_auto_payment(inv.pk)
                if result.get("success"):
                    retry_paid += 1
                    self._log(f"    [RETRY-OK] {inv.tenant.code}: {inv.invoice_number}")
                else:
                    retry_failed += 1
                    self._log(f"    [RETRY-FAIL] {inv.tenant.code}: {inv.invoice_number} "
                              f"reason={result.get('reason', '')[:80]}")
            self._log(f"    Result: retried_paid={retry_paid} retried_failed={retry_failed}")
        elif retry_invoices.exists() and not settings.TOSS_AUTO_BILLING_ENABLED:
            self._log(f"    Skipped: TOSS_AUTO_BILLING_ENABLED=OFF")

        # ──── 4. Exhausted -> OVERDUE ────
        self._log(f"\n  --- Step 4: OVERDUE transitions ---")
        max_attempts = settings.BILLING_RETRY_MAX_ATTEMPTS
        exhausted = Invoice.objects.filter(
            status="FAILED", attempt_count__gte=max_attempts, next_retry_at__isnull=True,
        ).exclude(tenant_id__in=exempt)

        overdue_count = 0
        for inv in exhausted:
            if not dry_run:
                invoice_service.mark_overdue(inv.pk)
            overdue_count += 1
            self._log(f"    {'[DRY] ' if dry_run else ''}OVERDUE: {inv.invoice_number}")
        self._log(f"    Result: {overdue_count} transitions")

        # ──── 5. Grace expired ────
        self._log(f"\n  --- Step 5: Grace -> Expired ---")
        grace_programs = all_live.filter(subscription_status="grace")
        expired_count = 0
        for program in grace_programs:
            if program.subscription_expires_at:
                grace_end = program.subscription_expires_at + timedelta(days=grace_days)
                if today > grace_end:
                    if not dry_run:
                        subscription_service.expire(program.pk)
                    expired_count += 1
                    self._log(f"    {'[DRY] ' if dry_run else ''}{program.tenant.code}: "
                              f"grace_end={grace_end} -> expired")
                else:
                    self._log(f"    {program.tenant.code}: grace until {grace_end} ({(grace_end - today).days}d left)")
        self._log(f"    Result: {expired_count} transitions")

        # ──── 6. Cancel at period end ────
        self._log(f"\n  --- Step 6: Cancel at period end ---")
        cancel_due = all_live.filter(cancel_at_period_end=True, subscription_status="active")
        cancel_count = 0
        for program in cancel_due:
            if program.subscription_expires_at and program.subscription_expires_at < today:
                if not dry_run:
                    subscription_service.expire(program.pk)
                cancel_count += 1
                self._log(f"    {'[DRY] ' if dry_run else ''}{program.tenant.code}: "
                          f"expires={program.subscription_expires_at} -> expired")
            else:
                self._log(f"    {program.tenant.code}: cancel scheduled, "
                          f"expires={program.subscription_expires_at} (not yet due)")
        self._log(f"    Result: {cancel_count} transitions")

        self._log("")
        self.stdout.write(self.style.SUCCESS("=== process_billing complete ==="))

    def _earliest_billing(self, qs):
        """Find earliest next_billing_at among given programs."""
        earliest = qs.filter(
            next_billing_at__isnull=False
        ).order_by("next_billing_at").first()
        if earliest:
            return f"{earliest.tenant.code} on {earliest.next_billing_at}"
        return "none set"

    def _log(self, msg):
        try:
            self.stdout.write(msg)
        except UnicodeEncodeError:
            self.stdout.write(msg.encode("ascii", errors="replace").decode("ascii"))
