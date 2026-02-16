# PATH: apps/core/management/commands/list_tenant_owners.py
"""
테넌트 코드별 Owner(멤버십) 목록 확인. 로그인 403 원인 파악용.

사용:
  python manage.py list_tenant_owners
  python manage.py list_tenant_owners tchul
  python manage.py list_tenant_owners --code=tchul
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.core.models import TenantMembership

User = get_user_model()


class Command(BaseCommand):
    help = "List owners (TenantMembership role=owner) for a tenant by code."

    def add_arguments(self, parser):
        parser.add_argument(
            "code",
            nargs="?",
            default=None,
            help="Tenant code (e.g. tchul). If omitted, list all tenants with owner count.",
        )

    def handle(self, *args, **options):
        from apps.core.models import Tenant
        from academy.adapters.db.django import repositories_core as core_repo

        code = options.get("code") or (args[0] if args else None)
        if code:
            tenant = core_repo.tenant_get_by_code(code)
            if not tenant:
                self.stderr.write(self.style.ERROR(f"Tenant code '{code}' not found."))
                return
            tenants = [tenant]
        else:
            tenants = list(Tenant.objects.filter(is_active=True).order_by("id"))

        for tenant in tenants:
            memberships = list(
                TenantMembership.objects.filter(
                    tenant=tenant, role="owner", is_active=True
                ).select_related("user")
            )
            self.stdout.write(
                self.style.HTTP_INFO(f"\n--- {tenant.code} (id={tenant.id}, {tenant.name}) ---")
            )
            if not memberships:
                self.stdout.write("  (no owners)")
                continue
            for m in memberships:
                u = m.user
                name = getattr(u, "name", "") or ""
                phone = getattr(u, "phone", "") or ""
                self.stdout.write(
                    f"  user_id={u.id}  username={u.username!r}  name={name!r}  phone={phone!r}"
                )
        self.stdout.write("")
