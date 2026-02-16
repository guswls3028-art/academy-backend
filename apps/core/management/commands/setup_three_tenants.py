# PATH: apps/core/management/commands/setup_three_tenants.py
"""
limglish.kr, tchul.com, ymath.co.kr 3개 도메인에 대응하는 테넌트·도메인·Program 셋업.

도메인은 이미 Cloudflare 등에 등록되어 있다고 가정.
- 테넌트 get_or_create (code=tchul, limglish, ymath)
- 각 테넌트에 Program get_or_create
- TenantDomain: tchul.com, www.tchul.com -> tchul / limglish.kr, www.limglish.kr -> limglish / ymath.co.kr, www.ymath.co.kr -> ymath

사용:
  python manage.py setup_three_tenants
"""
from django.core.management.base import BaseCommand

from academy.adapters.db.django import repositories_core as core_repo
from apps.core.models import Program

TENANTS_CONFIG = [
    {
        "code": "tchul",
        "name": "Tchul",
        "hosts": ["tchul.com", "www.tchul.com"],
        "primary_host": "tchul.com",
    },
    {
        "code": "limglish",
        "name": "Limglish",
        "hosts": ["limglish.kr", "www.limglish.kr"],
        "primary_host": "limglish.kr",
    },
    {
        "code": "ymath",
        "name": "Ymath",
        "hosts": ["ymath.co.kr", "www.ymath.co.kr"],
        "primary_host": "ymath.co.kr",
    },
]


class Command(BaseCommand):
    help = "Setup 3 tenants (tchul, limglish, ymath) and their domains (tchul.com, limglish.kr, ymath.co.kr)."

    def handle(self, *args, **options):
        for cfg in TENANTS_CONFIG:
            code = cfg["code"]
            name = cfg["name"]
            hosts = cfg["hosts"]
            primary_host = cfg["primary_host"]

            tenant, tenant_created = core_repo.tenant_get_or_create(
                code,
                defaults={"name": name, "is_active": True},
            )
            self.stdout.write(
                self.style.SUCCESS(f"Tenant: {tenant.code} (id={tenant.id}) {'created' if tenant_created else 'exists'}")
            )

            program, prog_created = core_repo.program_get_or_create(
                tenant,
                defaults={
                    "display_name": name,
                    "brand_key": code,
                    "login_variant": Program.LoginVariant.HAKWONPLUS,
                    "plan": Program.Plan.PREMIUM,
                    "feature_flags": {
                        "student_app_enabled": True,
                        "admin_enabled": True,
                    },
                    "ui_config": {"login_title": f"{name} 로그인"},
                    "is_active": True,
                },
            )
            self.stdout.write(f"  Program: {'created' if prog_created else 'exists'}")

            # Signal이 host=code 로 이미 primary 도메인을 만들었을 수 있음 → primary 해제 후 우리 도메인만 primary 사용
            existing_domains = core_repo.tenant_domain_filter_by_tenant(tenant)
            for d in existing_domains:
                if d.is_primary and d.host != primary_host:
                    d.is_primary = False
                    d.save()
                    self.stdout.write(self.style.WARNING(f"  TenantDomain: {d.host} -> is_primary=False (기존 해제)"))

            for host in hosts:
                host = host.strip().lower()
                if not host:
                    continue
                domain, dom_created = core_repo.tenant_domain_get_or_create_by_defaults(
                    host,
                    defaults={
                        "tenant": tenant,
                        "is_primary": host == primary_host,
                        "is_active": True,
                    },
                )
                if dom_created:
                    self.stdout.write(self.style.SUCCESS(f"  TenantDomain: {host} -> {tenant.code} (created)"))
                else:
                    if domain.tenant_id != tenant.id or not domain.is_active:
                        domain.tenant = tenant
                        domain.is_primary = host == primary_host
                        domain.is_active = True
                        domain.save()
                        self.stdout.write(self.style.WARNING(f"  TenantDomain: {host} -> {tenant.code} (updated)"))
                    else:
                        self.stdout.write(f"  TenantDomain: {host} -> {tenant.code} (exists)")

        self.stdout.write(
            self.style.SUCCESS("Done. limglish.kr, tchul.com, ymath.co.kr tenants and domains are set.")
        )
