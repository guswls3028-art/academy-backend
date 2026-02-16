# PATH: apps/core/management/commands/ensure_api_domain.py
"""
프로덕션 API 호스트(api.hakwonplus.com 등)를 테넌트에 연결.

- 프론트가 https://api.hakwonplus.com 으로 API 호출 시 Host=api.hakwonplus.com
- TenantDomain에 api.hakwonplus.com 이 없으면 "Tenant not found for host" 404
- 이 명령을 한 번 실행하면 api.hakwonplus.com(및 선택 host)이 지정 테넌트에 연결됨.

사용:
  python manage.py ensure_api_domain
  python manage.py ensure_api_domain --tenant=1
  python manage.py ensure_api_domain --tenant=1 --host=api.hakwonplus.com
"""
from django.core.management.base import BaseCommand

from academy.adapters.db.django import repositories_core as core_repo

DEFAULT_API_HOSTS = (
    "api.hakwonplus.com",
    "www.hakwonplus.com",
    "hakwonplus.com",
)


class Command(BaseCommand):
    help = "Ensure API/production hosts (e.g. api.hakwonplus.com) are mapped to a tenant."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=int,
            default=1,
            help="Tenant id to attach hosts to (default: 1)",
        )
        parser.add_argument(
            "--host",
            type=str,
            action="append",
            dest="hosts",
            default=None,
            help="Host to add (can repeat). If not set, uses api.hakwonplus.com, www.hakwonplus.com, hakwonplus.com",
        )

    def handle(self, *args, **options):
        tenant_id = options.get("tenant")
        tenant = core_repo.tenant_get_by_id_any(tenant_id)
        if not tenant:
            self.stderr.write(
                self.style.ERROR(f"Tenant id={tenant_id} not found.")
            )
            return

        hosts = options.get("hosts") or list(DEFAULT_API_HOSTS)
        for host in hosts:
            host = (host or "").strip().lower()
            if not host:
                continue
            domain, created = core_repo.tenant_domain_get_or_create_by_defaults(
                host,
                defaults={
                    "tenant": tenant,
                    "is_primary": (host == "hakwonplus.com"),
                    "is_active": True,
                },
            )
            if created:
                self.stdout.write(
                    self.style.SUCCESS(f"Created TenantDomain: {host} -> {tenant.code}")
                )
            else:
                if domain.tenant_id != tenant.id or not domain.is_active:
                    domain.tenant = tenant
                    domain.is_active = True
                    domain.save()
                    self.stdout.write(
                        self.style.WARNING(f"Updated TenantDomain: {host} -> {tenant.code}")
                    )
                else:
                    self.stdout.write(f"Already exists: {host} -> {tenant.code}")

        self.stdout.write(
            self.style.SUCCESS("Done. API requests to these hosts will resolve to this tenant.")
        )
