"""Fail-closed read-only audit for a newly provisioned tenant."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Program, Tenant, TenantDomain, TenantMembership


@dataclass(frozen=True)
class AuditResult:
    key: str
    passed: bool
    detail: str


def _normalized_apex(value: str) -> str:
    domain = str(value or "").strip().lower().rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain or "://" in domain or "/" in domain or ":" in domain:
        raise CommandError("domain must be an apex hostname such as example.com")
    return domain


class Command(BaseCommand):
    help = "Read-only fail-closed audit for a newly provisioned tenant."

    def add_arguments(self, parser):
        parser.add_argument("code", type=str)
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--domain", type=str, required=True)
        parser.add_argument(
            "--billing-mode",
            choices=("contract", "exempt"),
            required=True,
            help="contract requires active dated access; exempt requires the runtime exemption.",
        )
        parser.add_argument(
            "--messaging-mode",
            choices=("disabled", "approved"),
            default="disabled",
            help="New tenants stay disabled unless messaging activation was separately approved.",
        )
        parser.add_argument(
            "--require-owner",
            action="store_true",
            help="Require exactly one active owner membership and active user.",
        )

    def handle(self, *args, **options):
        code = str(options["code"] or "").strip().lower()
        tenant_id = int(options["tenant_id"])
        apex = _normalized_apex(options["domain"])
        www = f"www.{apex}"
        results: list[AuditResult] = []

        def record(key: str, passed: bool, detail: str) -> None:
            results.append(AuditResult(key=key, passed=bool(passed), detail=detail))

        tenant = Tenant.objects.filter(code=code).first()
        if tenant is None:
            record("tenant.exists", False, f"code={code!r} was not found")
            return self._finish(results)

        record(
            "tenant.identity",
            tenant.id == tenant_id,
            f"expected id={tenant_id}, actual id={tenant.id}",
        )
        record("tenant.active", tenant.is_active, f"is_active={tenant.is_active}")

        domains = {
            row.host: row
            for row in TenantDomain.objects.filter(host__in=(apex, www)).select_related(
                "tenant"
            )
        }
        apex_row = domains.get(apex)
        www_row = domains.get(www)
        record(
            "domain.apex",
            bool(
                apex_row
                and apex_row.tenant_id == tenant.id
                and apex_row.is_active
                and apex_row.is_primary
            ),
            self._domain_detail(apex_row),
        )
        record(
            "domain.www",
            bool(
                www_row
                and www_row.tenant_id == tenant.id
                and www_row.is_active
                and not www_row.is_primary
            ),
            self._domain_detail(www_row),
        )

        hosts = {str(value).strip().lower() for value in settings.ALLOWED_HOSTS}
        cors_origins = {
            str(value).strip().lower()
            for value in getattr(settings, "CORS_ALLOWED_ORIGINS", [])
        }
        csrf_origins = {
            str(value).strip().lower()
            for value in getattr(settings, "CSRF_TRUSTED_ORIGINS", [])
        }
        record(
            "runtime.allowed_hosts",
            {apex, www}.issubset(hosts),
            f"required={apex},{www}",
        )
        required_origins = {f"https://{apex}", f"https://{www}"}
        record(
            "runtime.cors",
            required_origins.issubset(cors_origins),
            f"required={','.join(sorted(required_origins))}",
        )
        record(
            "runtime.csrf",
            required_origins.issubset(csrf_origins),
            f"required={','.join(sorted(required_origins))}",
        )

        program = Program.objects.filter(tenant=tenant).first()
        if program is None:
            record("program.exists", False, "Program row is missing")
        else:
            record(
                "program.core",
                bool(
                    program.is_active
                    and program.plan == Program.Plan.ALL
                    and program.brand_key == code
                    and program.login_variant == Program.LoginVariant.HAKWONPLUS
                ),
                f"active={program.is_active} plan={program.plan} "
                f"brand_key={program.brand_key} login_variant={program.login_variant}",
            )
            ui_config = dict(program.ui_config or {})
            required_branding = (
                "login_title",
                "login_subtitle",
                "window_title",
                "logo_url",
                "primary_color",
            )
            missing_branding = [key for key in required_branding if not ui_config.get(key)]
            record(
                "program.branding",
                not missing_branding,
                "complete" if not missing_branding else f"missing={','.join(missing_branding)}",
            )
            feature_flags = dict(program.feature_flags or {})
            missing_features = [
                key
                for key in ("admin_enabled", "student_app_enabled")
                if feature_flags.get(key) is not True
            ]
            record(
                "program.features",
                not missing_features,
                "complete" if not missing_features else f"disabled={','.join(missing_features)}",
            )
            self._record_billing_result(
                record=record,
                program=program,
                tenant_id=tenant.id,
                billing_mode=options["billing_mode"],
            )

        owner_memberships = list(
            TenantMembership.objects.filter(
                tenant=tenant,
                role="owner",
                is_active=True,
            ).select_related("user")
        )
        active_owners = [row for row in owner_memberships if row.user.is_active]
        if options["require_owner"]:
            owner_ok = len(owner_memberships) == 1 and len(active_owners) == 1
            owner_detail = (
                f"active_memberships={len(owner_memberships)} "
                f"active_users={len(active_owners)}"
            )
            record("owner.ready", owner_ok, owner_detail)
        else:
            record(
                "owner.not_duplicated",
                len(owner_memberships) <= 1,
                f"active_memberships={len(owner_memberships)}",
            )

        expected_messaging_active = options["messaging_mode"] == "approved"
        record(
            "messaging.activation",
            tenant.messaging_is_active is expected_messaging_active,
            f"expected={options['messaging_mode']} actual_active={tenant.messaging_is_active}",
        )
        record(
            "safety.defaults",
            bool(
                not tenant.student_registration_auto_approve
                and not tenant.clinic_auto_approve_booking
                and tenant.video_max_sessions == 0
                and tenant.video_max_devices == 0
            ),
            "student_auto_approve=false clinic_auto_approve=false "
            f"video_sessions={tenant.video_max_sessions} "
            f"video_devices={tenant.video_max_devices}",
        )

        return self._finish(results)

    @staticmethod
    def _domain_detail(row: TenantDomain | None) -> str:
        if row is None:
            return "missing"
        return (
            f"tenant={row.tenant.code} active={row.is_active} "
            f"primary={row.is_primary}"
        )

    @staticmethod
    def _record_billing_result(*, record, program, tenant_id, billing_mode) -> None:
        exempt_ids = set(
            getattr(settings, "BILLING_EXEMPT_TENANT_IDS", set()) or set()
        )
        if billing_mode == "exempt":
            passed = bool(
                tenant_id in exempt_ids
                and program.is_subscription_active
                and program.subscription_expires_at is None
                and program.next_billing_at is None
            )
        else:
            passed = bool(
                tenant_id not in exempt_ids
                and program.is_subscription_active
                and program.subscription_expires_at is not None
                and program.next_billing_at is not None
            )
        record(
            "billing.access",
            passed,
            f"mode={billing_mode} exempt={tenant_id in exempt_ids} "
            f"status={program.subscription_status} "
            f"expires={program.subscription_expires_at} next={program.next_billing_at} "
            f"active={program.is_subscription_active}",
        )

    def _finish(self, results: list[AuditResult]):
        failures = [result for result in results if not result.passed]
        for result in results:
            label = "PASS" if result.passed else "FAIL"
            writer = self.stdout.write if result.passed else self.stderr.write
            writer(f"[{label}] {result.key}: {result.detail}")
        if failures:
            failed_keys = ",".join(result.key for result in failures)
            raise CommandError(
                f"TENANT_ONBOARDING_AUDIT_FAILED failures={len(failures)} "
                f"keys={failed_keys}"
            )
        self.stdout.write(self.style.SUCCESS("TENANT_ONBOARDING_AUDIT_PASS"))
