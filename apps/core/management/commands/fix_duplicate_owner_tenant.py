# PATH: apps/core/management/commands/fix_duplicate_owner_tenant.py
"""
한 User가 여러 테넌트의 owner인 경우, 테넌트별로 별도 User가 있도록 복구.
attach_owner_users_to_tenant 실행 후 user_id=1이 9999로만 붙어 1번 로그인이 깨진 경우:
- tenant 1용 User(t1_admin97) 생성, 기존 user_id=1의 password 해시 복사
- TenantMembership(tenant=1)을 새 User로 변경

사용: python manage.py fix_duplicate_owner_tenant --apply
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model

from apps.core.models import TenantMembership
from academy.adapters.db.django import repositories_core as core_repo

User = get_user_model()


class Command(BaseCommand):
    help = "Fix: create per-tenant owner user when one user was owner of multiple tenants."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        apply = options.get("apply", False)
        if not apply:
            self.stdout.write(self.style.WARNING("Dry-run. Use --apply to save."))

        tenant_1 = core_repo.tenant_get_by_code("hakwonplus")
        if not tenant_1:
            self.stderr.write(self.style.ERROR("Tenant hakwonplus not found."))
            return

        # 1번 테넌트에 t1_admin97 유저가 있는지
        u1 = core_repo.user_get_by_tenant_username(tenant_1, "admin97")
        if u1:
            self.stdout.write(f"Tenant 1 already has user: id={u1.pk} username={u1.username}")
            return

        # 1번 오너 멤버십 (현재 user_id=1 등)
        m1 = TenantMembership.objects.filter(tenant=tenant_1, role="owner", is_active=True).select_related("user").first()
        if not m1 or not m1.user:
            self.stderr.write(self.style.ERROR("No owner membership for tenant 1."))
            return

        source_user = m1.user
        self.stdout.write(f"Source user: id={source_user.pk} username={source_user.username} tenant_id={source_user.tenant_id}")

        new_username = f"t{tenant_1.id}_admin97"
        if User.objects.filter(username=new_username).exists():
            self.stdout.write(f"User {new_username!r} already exists, updating membership only.")
            new_user = User.objects.get(username=new_username)
            if apply and m1.user_id != new_user.pk:
                m1.user = new_user
                m1.save(update_fields=["user_id"])
                self.stdout.write(self.style.SUCCESS(f"Tenant 1 membership -> user_id={new_user.pk}"))
            return

        if apply:
            with transaction.atomic():
                new_user = User(
                    username=new_username,
                    tenant=tenant_1,
                    email=source_user.email or "",
                    name=getattr(source_user, "name", "") or "",
                    phone=getattr(source_user, "phone", "") or "",
                    is_active=True,
                    is_staff=source_user.is_staff,
                    is_superuser=source_user.is_superuser,
                )
                new_user.password = source_user.password  # hash 복사
                new_user.save()
                m1.user = new_user
                m1.save(update_fields=["user_id"])
            self.stdout.write(self.style.SUCCESS(f"Created user id={new_user.pk} {new_username!r}, tenant 1 membership updated."))
        else:
            self.stdout.write(f"  [would] Create {new_username!r}, copy password hash, point tenant 1 membership to new user.")
