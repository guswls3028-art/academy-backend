"""
Billing fields audit + optional fix.

Checks:
1. next_billing_at NULL (active/grace, non-exempt)
2. subscription_expires_at NULL (active/grace, non-exempt)
3. monthly_price vs PLAN_PRICES (informational)

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

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

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

    def handle(self, *args, **options):
        fix = options["fix_next_billing"]
        tenant_code = options["tenant"]
        all_live = options["all_live"]
        confirm_live = options["confirm_live"]
        dry_run = options["dry_run"]
        exempt = settings.BILLING_EXEMPT_TENANT_IDS

        # ── Validate fix options ──
        if fix and not tenant_code and not all_live:
            raise CommandError(
                "--fix-next-billing requires --tenant <code> or --all-live to specify scope.\n"
                "Examples:\n"
                "  --fix-next-billing --tenant limglish --confirm-live\n"
                "  --fix-next-billing --all-live --confirm-live --dry-run"
            )

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
                    self._log(f"  [WARN]   {code:15s} subscription_expires_at=NULL (active/grace should have a date)")

            # 3. price vs PLAN_PRICES (informational)
            standard_price = Program.PLAN_PRICES.get(p.plan)
            if standard_price and p.monthly_price != standard_price:
                self._log(f"  [INFO]   {code:15s} price={p.monthly_price:,} vs standard={standard_price:,} "
                          f"(promo or manual override)")

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

    def _log(self, msg):
        try:
            self.stdout.write(msg)
        except UnicodeEncodeError:
            self.stdout.write(msg.encode("ascii", errors="replace").decode("ascii"))
