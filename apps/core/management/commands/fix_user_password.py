# 프로덕션 유저 비밀번호 복구/생성용.
# username은 "표시용 아이디" (예: admin97). 내부적으로 t{tenant_id}_ 접두사 자동 적용.
# Usage: python manage.py fix_user_password --username=admin97 --password=kjkszpj123 --tenant-code=hakwonplus
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Reset/create user password (tenant-scoped, auto-prefixes username)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Display username (e.g. admin97)")
        parser.add_argument("--password", required=True)
        parser.add_argument("--tenant-code", required=True)
        parser.add_argument("--name", default=None, help="Set display name")
        parser.add_argument("--role", default="owner", help="Membership role (default: owner)")
        parser.add_argument("--cleanup-bare", action="store_true",
                            help="Delete bare username (without t{id}_ prefix) if it exists")

    def handle(self, *args, **options):
        from apps.core.models import Tenant, TenantMembership
        from apps.core.models.user import user_internal_username

        User = get_user_model()
        tc = options["tenant_code"].strip()
        display_uname = options["username"].strip()
        pw = options["password"].strip()
        name = options.get("name")
        role = options.get("role", "owner")

        tenant = Tenant.objects.filter(code__iexact=tc, is_active=True).first()
        if not tenant:
            self.stderr.write(f"Tenant '{tc}' not found or inactive.")
            return

        # Internal username: t{tenant_id}_{display}
        internal_uname = user_internal_username(tenant, display_uname)
        self.stdout.write(f"Display: {display_uname} -> Internal: {internal_uname}")

        # Cleanup bare username (without prefix) if requested
        if options.get("cleanup_bare"):
            bare = User.objects.filter(username=display_uname).first()
            if bare and bare.username != internal_uname:
                bare.delete()
                self.stdout.write(self.style.WARNING(f"Deleted bare user '{display_uname}' (id={bare.id})"))

        user = User.objects.filter(username=internal_uname).first()
        if not user:
            user = User(
                username=internal_uname,
                tenant=tenant,
                is_active=True,
                is_staff=True,
            )
            if name:
                user.name = name
            user.set_password(pw)
            user.save()
            TenantMembership.objects.get_or_create(
                tenant=tenant, user=user,
                defaults={"role": role, "is_active": True},
            )
            self.stdout.write(self.style.SUCCESS(
                f"CREATED user '{internal_uname}' on tenant '{tc}' (id={user.id})"
            ))
        else:
            user.set_password(pw)
            user.is_active = True
            user.tenant = tenant
            fields = ["password", "is_active", "tenant"]
            if name:
                user.name = name
                fields.append("name")
            user.save(update_fields=fields)
            mem, _ = TenantMembership.objects.get_or_create(
                tenant=tenant, user=user,
                defaults={"role": role, "is_active": True},
            )
            if not mem.is_active:
                mem.is_active = True
                mem.save(update_fields=["is_active"])
            self.stdout.write(self.style.SUCCESS(
                f"RESET password for '{internal_uname}' on tenant '{tc}' (id={user.id}, role={mem.role})"
            ))
