"""
Billing fields audit + optional fix.

Checks:
1. next_billing_at NULL (active/grace, non-exempt)
2. subscription_expires_at NULL (active/grace, non-exempt)
3. persisted active/grace status past its effective access period
4. contract tenant monthly_price integrity (blocking) + other overrides (informational)
5. plaintext or undecryptable billing credentials

Usage:
  python manage.py audit_billing_fields                            # audit only (read-only)
  python manage.py audit_billing_fields --dry-run --fix-next-billing  # preview fix
  python manage.py audit_billing_fields --fix-next-billing --tenant limglish --confirm-live
  python manage.py audit_billing_fields --fix-next-billing --all-live --confirm-live

Safety:
  - Default is read-only audit. No writes.
  - --fix-next-billing requires either --tenant or --all-live to specify scope.
  - LIVE tenants require --confirm-live.
  - --dry-run shows what would change without writing.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.billing.models import BillingKey, PaymentTransaction
from apps.billing.services.billing_key_crypto import (
    ENCRYPTED_PREFIX,
    BillingKeyCryptoError,
    decrypt_billing_key,
)
from apps.core.models import Tenant
from apps.core.models.program import Program


class Command(BaseCommand):
    help = "Billing fields audit: next_billing_at, expires_at, price consistency"

    def add_arguments(self, parser):
        parser.add_argument("--fix-next-billing", action="store_true",
                            help="Fix NULL next_billing_at (requires --tenant or --all-live)")
        parser.add_argument("--tenant", help="Limit to specific tenant code")
        parser.add_argument("--all-live", action="store_true",
                            help="Apply fix to ALL live tenants (use with --confirm-live)")
        parser.add_argument("--confirm-live", action="store_true",
                            help="Required to modify live tenants")
        parser.add_argument("--dry-run", action="store_true",
                            help="Preview changes without writing")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero when any audit issue is found (read-only only).",
        )

    def handle(self, *args, **options):
        fix = options["fix_next_billing"]
        tenant_code = options["tenant"]
        all_live = options["all_live"]
        confirm_live = options["confirm_live"]
        dry_run = options["dry_run"]
        strict = options["strict"]
        exempt = settings.BILLING_EXEMPT_TENANT_IDS
        today = timezone.localdate()

        # ── Validate fix options ──
        if fix and not tenant_code and not all_live:
            raise CommandError(
                "--fix-next-billing requires --tenant <code> or --all-live to specify scope.\n"
                "Examples:\n"
                "  --fix-next-billing --tenant limglish --confirm-live\n"
                "  --fix-next-billing --all-live --confirm-live --dry-run"
            )
        if strict and fix:
            raise CommandError("--strict cannot be combined with fix options.")

        # ── Load programs ──
        programs = Program.objects.select_related("tenant").order_by("tenant__code")
        if tenant_code:
            programs = programs.filter(tenant__code=tenant_code)
            if not programs.exists():
                raise CommandError(f"Tenant '{tenant_code}' not found.")

        self._log(f"=== Billing Fields Audit {'[DRY RUN]' if dry_run else ''} ===")
        self._log("")

        # ── Audit: find issues ──
        issues = []
        missing_program_tenants = Tenant.objects.exclude(id__in=exempt).filter(
            program__isnull=True,
            is_active=True,
        )
        for tenant in missing_program_tenants:
            issues.append({
                "program": None,
                "code": tenant.code,
                "is_exempt": False,
                "msg": (
                    f"  [LIVE]   {tenant.code:15s} Program missing "
                    "(subscription access is fail-closed)"
                ),
                "fix_value": None,
            })
        for p in programs:
            code = p.tenant.code
            is_ex = p.tenant_id in exempt
            tag = "[EXEMPT]" if is_ex else "[LIVE]  "
            prefix = f"  {tag} {code:15s}"

            # 1. next_billing_at NULL (non-exempt, active/grace)
            if not is_ex and p.subscription_status in ("active", "grace"):
                if p.next_billing_at is None:
                    if p.subscription_expires_at:
                        issues.append({
                            "program": p, "code": code, "is_exempt": is_ex,
                            "msg": f"{prefix} next_billing_at=NULL (expires={p.subscription_expires_at})",
                            "fix_value": p.subscription_expires_at,
                        })
                    else:
                        issues.append({
                            "program": p, "code": code, "is_exempt": is_ex,
                            "msg": f"{prefix} next_billing_at=NULL AND expires_at=NULL (cannot auto-fix)",
                            "fix_value": None,
                        })

            # 2. expires_at NULL (non-exempt, active/grace)
            if not is_ex and p.subscription_status in ("active", "grace"):
                if p.subscription_expires_at is None:
                    if not any(
                        issue["program"] == p and "expires_at=NULL" in issue["msg"]
                        for issue in issues
                    ):
                        issues.append({
                            "program": p,
                            "code": code,
                            "is_exempt": is_ex,
                            "msg": (
                                f"{prefix} subscription_expires_at=NULL "
                                "(subscription access is fail-closed)"
                            ),
                            "fix_value": None,
                        })

            # 3. persisted state must agree with the effective access period.
            if (
                not is_ex
                and p.subscription_status == "active"
                and p.subscription_expires_at is not None
                and p.subscription_expires_at < today
            ):
                issues.append({
                    "program": p,
                    "code": code,
                    "is_exempt": is_ex,
                    "msg": (
                        f"{prefix} active_subscription_past_expiry: "
                        f"expires={p.subscription_expires_at} "
                        "(run process_billing; effective access is fail-closed)"
                    ),
                    "fix_value": None,
                })
            if (
                not is_ex
                and p.subscription_status == "grace"
                and p.subscription_expires_at is not None
                and today
                > p.subscription_expires_at
                + timedelta(days=int(settings.BILLING_GRACE_PERIOD_DAYS))
            ):
                issues.append({
                    "program": p,
                    "code": code,
                    "is_exempt": is_ex,
                    "msg": (
                        f"{prefix} grace_subscription_past_access: "
                        f"expires={p.subscription_expires_at} "
                        "(run process_billing; effective access is fail-closed)"
                    ),
                    "fix_value": None,
                })

            # 4. price vs plan/contract policy
            expected_price = Program.resolve_monthly_price(plan=p.plan, tenant_code=code)
            contract_price = Program.get_contract_monthly_price(code)
            if contract_price is not None and p.monthly_price != contract_price:
                issues.append({
                    "program": p,
                    "code": code,
                    "is_exempt": is_ex,
                    "msg": (
                        f"{prefix} contract_price_mismatch: "
                        f"price={p.monthly_price:,} expected={contract_price:,} "
                        "(new invoice creation is blocked)"
                    ),
                    "fix_value": None,
                })
            elif expected_price and p.monthly_price != expected_price:
                self._log(f"  [INFO]   {code:15s} price={p.monthly_price:,} vs expected={expected_price:,} "
                          f"(promo or manual override)")

        processing_transactions = (
            PaymentTransaction.objects.exclude(tenant_id__in=exempt)
            .filter(status="PROCESSING")
            .select_related("tenant", "invoice")
            .order_by("created_at", "id")
        )
        if tenant_code:
            processing_transactions = processing_transactions.filter(
                tenant__code=tenant_code
            )
        for payment in processing_transactions:
            invoice_number = (
                payment.invoice.invoice_number if payment.invoice_id else "none"
            )
            issues.append({
                "program": getattr(payment.tenant, "program", None),
                "code": payment.tenant.code,
                "is_exempt": False,
                "msg": (
                    f"  [LIVE]   {payment.tenant.code:15s} "
                    f"payment_processing_unresolved tx={payment.id} "
                    f"invoice={invoice_number} started={payment.processing_started_at} "
                    "(provider reconciliation required; do not auto-retry)"
                ),
                "fix_value": None,
            })

        billing_keys = BillingKey.objects.select_related("tenant")
        if tenant_code:
            billing_keys = billing_keys.filter(tenant__code=tenant_code)
        for billing_key in billing_keys.order_by("tenant__code", "id"):
            if billing_key.billing_key.startswith(ENCRYPTED_PREFIX):
                try:
                    decrypt_billing_key(billing_key.billing_key)
                except BillingKeyCryptoError:
                    issues.append({
                        "program": getattr(billing_key.tenant, "program", None),
                        "code": billing_key.tenant.code,
                        "is_exempt": billing_key.tenant_id in exempt,
                        "msg": (
                            f"  [SECURITY] {billing_key.tenant.code:15s} "
                            f"undecryptable_billing_key id={billing_key.id} "
                            "(credential keyring/configuration repair required)"
                        ),
                        "fix_value": None,
                    })
                continue
            if settings.BILLING_KEY_ENCRYPTION_WRITE_ENABLED:
                issues.append({
                    "program": getattr(billing_key.tenant, "program", None),
                    "code": billing_key.tenant.code,
                    "is_exempt": billing_key.tenant_id in exempt,
                    "msg": (
                        f"  [SECURITY] {billing_key.tenant.code:15s} "
                        f"plaintext_billing_key id={billing_key.id} "
                        "(encrypted writes are enabled; rotate or re-encrypt before use)"
                    ),
                    "fix_value": None,
                })

        # ── Report issues ──
        self._log("")
        fixable = [i for i in issues if i["fix_value"] is not None]
        unfixable = [i for i in issues if i["fix_value"] is None]

        if not issues:
            self._log("No issues found. All billing fields consistent.")
        else:
            self._log(f"Issues found: {len(issues)} ({len(fixable)} fixable, {len(unfixable)} manual)")
            for i in issues:
                self._log(i["msg"])

        # ── Apply fixes ──
        if fix and fixable:
            self._log("")

            # Check LIVE tenant safety
            live_targets = [i for i in fixable if not i["is_exempt"]]
            if live_targets and not confirm_live and not dry_run:
                codes = ", ".join(i["code"] for i in live_targets)
                raise CommandError(
                    f"Fix targets include LIVE tenant(s): {codes}\n"
                    f"Use --confirm-live to proceed, or --dry-run to preview."
                )

            self._log(f"--- Fixing next_billing_at {'[DRY RUN]' if dry_run else ''} ---")
            fixed = 0
            for i in fixable:
                p = i["program"]
                old_val = p.next_billing_at
                new_val = i["fix_value"]
                tag = "[EXEMPT]" if i["is_exempt"] else "[LIVE]  "

                self._log(f"  {tag} {i['code']:15s}  "
                          f"BEFORE: next_billing_at={old_val}  "
                          f"AFTER: next_billing_at={new_val}")

                if not dry_run:
                    p.next_billing_at = new_val
                    p.save(update_fields=["next_billing_at", "updated_at"])
                    fixed += 1

            if dry_run:
                self._log(f"\n  [DRY RUN] Would fix {len(fixable)} field(s). No changes applied.")
            else:
                self._log(f"\n  Fixed {fixed} next_billing_at field(s).")

        elif fix and not fixable:
            self._log("\nNo fixable issues found.")

        # ── Summary table ──
        # Reload if we fixed things
        if fix and not dry_run:
            programs = Program.objects.select_related("tenant").order_by("tenant__code")
            if tenant_code:
                programs = programs.filter(tenant__code=tenant_code)

        self._log(f"\n=== Current State ===")
        self._log(f"  {'Tenant':15s} {'Status':8s} {'Plan':8s} {'Price':>9s} "
                  f"{'Mode':16s} {'Expires':12s} {'NextBill':12s}")
        self._log(f"  {'-'*15} {'-'*8} {'-'*8} {'-'*9} {'-'*16} {'-'*12} {'-'*12}")
        for p in programs:
            is_ex = p.tenant_id in exempt
            tag = "*" if is_ex else " "
            self._log(
                f" {tag}{p.tenant.code:15s} {p.subscription_status:8s} {p.plan:8s} "
                f"{p.monthly_price:>9,} {p.billing_mode:16s} "
                f"{str(p.subscription_expires_at or 'NULL'):12s} "
                f"{str(p.next_billing_at or 'NULL'):12s}"
            )

        if strict and issues:
            raise CommandError(f"billing_audit_strict_failed:issue_count={len(issues)}")

    def _log(self, msg):
        try:
            self.stdout.write(msg)
        except UnicodeEncodeError:
            self.stdout.write(msg.encode("ascii", errors="replace").decode("ascii"))
