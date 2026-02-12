# PATH: apps/core/management/commands/ensure_localhost_tenant.py
"""
로컬 개발 시 localhost → tenant 연결.

- Host 기반 tenant resolver는 DB의 TenantDomain만 사용함.
- localhost:8000 으로 접속하면 host='localhost' 로 조회하는데,
  해당 TenantDomain이 없으면 /api/v1/core/me/, /api/v1/core/program/ 등이 404.
- 이 명령을 한 번 실행하면 localhost(및 127.0.0.1)가 첫 번째 tenant에 연결됨.

사용:
  python manage.py ensure_localhost_tenant
"""
from django.core.management.base import BaseCommand

from apps.core.models import Tenant, TenantDomain


def normalize_host(host: str) -> str:
    v = str(host or "").strip().lower()
    if not v:
        return ""
    return v.split(":")[0].strip()


class Command(BaseCommand):
    help = "Ensure localhost (and 127.0.0.1) are mapped to a tenant for local dev."

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(is_active=True).order_by("id").first()
        if not tenant:
            self.stderr.write(
                self.style.ERROR("No active Tenant found. Create a tenant first (e.g. via admin or migration).")
            )
            return

        for host in ("localhost", "127.0.0.1"):
            domain, created = TenantDomain.objects.get_or_create(
                host=host,
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
