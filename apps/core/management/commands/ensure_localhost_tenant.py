# PATH: apps/core/management/commands/ensure_localhost_tenant.py
"""
로컬 개발 시 localhost → tenant 연결.

- Host 기반 tenant resolver는 DB의 TenantDomain만 사용함.
- localhost:8000 으로 접속하면 host='localhost' 로 조회하는데,
  해당 TenantDomain이 없으면 /api/v1/core/me/, /api/v1/core/program/ 등이 404.
- 이 명령을 한 번 실행하면 localhost(및 127.0.0.1)가 지정 tenant(또는 첫 번째)에 연결됨.

사용:
  python manage.py ensure_localhost_tenant
  python manage.py ensure_localhost_tenant --tenant=1   # 1번 테넌트 개발용 고정
"""
from django.core.management.base import BaseCommand

from academy.adapters.db.django import repositories_core as core_repo


class Command(BaseCommand):
    help = "Ensure localhost (and 127.0.0.1) are mapped to a tenant for local dev."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=int,
            default=None,
            help="지정 테넌트 id로 고정 (미지정 시 첫 번째 활성 테넌트)",
        )

    def handle(self, *args, **options):
        tenant_id = options.get("tenant")
        if tenant_id is not None:
            tenant = core_repo.tenant_get_by_id(tenant_id)
            if not tenant:
                self.stderr.write(
                    self.style.ERROR(f"Tenant id={tenant_id} not found or inactive.")
                )
                return
        else:
            tenant = core_repo.tenant_first_active()
        if not tenant:
            self.stderr.write(
                self.style.ERROR("No active Tenant found. Create a tenant first (e.g. via admin or migration).")
            )
            return

        for host in ("localhost", "127.0.0.1"):
            domain, created = core_repo.tenant_domain_get_or_create_by_defaults(
                host,
                defaults={
                    "tenant": tenant,
                    "is_primary": False,
                    "is_active": True,
                },
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created TenantDomain: {host} -> {tenant.code}"))
            else:
                if domain.tenant_id != tenant.id:
                    domain.tenant = tenant
                    domain.is_active = True
                    domain.save()
                    self.stdout.write(self.style.WARNING(f"Updated TenantDomain: {host} -> {tenant.code}"))
                else:
                    self.stdout.write(f"Already exists: {host} -> {tenant.code}")

        self.stdout.write(
            self.style.SUCCESS("Done. Requests to http://localhost:8000 will now resolve to this tenant.")
        )
