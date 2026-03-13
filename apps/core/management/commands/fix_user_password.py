# One-shot: 프로덕션 유저 비밀번호 복구용.
# Usage: python manage.py fix_user_password --username=admin97 --password=kjkszpj123 --tenant-code=hakwonplus
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Reset user password (tenant-scoped)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--password", required=True)
        parser.add_argument("--tenant-code", required=True)
        parser.add_argument("--name", default=None, help="Set display name")

    def handle(self, *args, **options):
        from apps.core.models import Tenant, TenantMembership

        User = get_user_model()
        tc = options["tenant_code"].strip()
        uname = options["username"].strip()
        pw = options["password"].strip()
        name = options.get("name")

        tenant = Tenant.objects.filter(code__iexact=tc, is_active=True).first()
        if not tenant:
            self.stderr.write(f"Tenant '{tc}' not found or inactive.")
            return

        user = User.objects.filter(username=uname).first()
        if not user:
            # Create user + membership
            user = User.objects.create_user(
                username=uname,
                password=pw,
                is_active=True,
                is_staff=True,
                tenant=tenant,
            )
            if name:
                user.name = name
                user.save(update_fields=["name"])
            TenantMembership.objects.get_or_create(
                tenant=tenant, user=user,
                defaults={"role": "admin", "is_active": True},
            )
            self.stdout.write(self.style.SUCCESS(
                f"CREATED user '{uname}' on tenant '{tc}' (id={user.id})"
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
            # Ensure membership
            mem, _ = TenantMembership.objects.get_or_create(
                tenant=tenant, user=user,
                defaults={"role": "admin", "is_active": True},
            )
            if not mem.is_active:
                mem.is_active = True
                mem.save(update_fields=["is_active"])
            self.stdout.write(self.style.SUCCESS(
                f"RESET password for '{uname}' on tenant '{tc}' (id={user.id}, role={mem.role})"
            ))
