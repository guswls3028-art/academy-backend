# 프로덕션 유저 비밀번호 복구/생성용.
# username은 "표시용 아이디" (예: admin97). 내부적으로 t{tenant_id}_ 접두사 자동 적용.
# Usage: python manage.py fix_user_password --username=<id> --password=<temporary-password> --tenant-code=<code>
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction


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
        from apps.core.models import Tenant
        from apps.core.models.user import user_internal_username
        from academy.adapters.db.django import repositories_core as core_repo

        User = get_user_model()
        tc = options["tenant_code"].strip()
        display_uname = options["username"].strip()
        pw = str(options["password"])
        name = options.get("name")
        role = options.get("role", "owner")

        tenant = Tenant.objects.filter(code__iexact=tc, is_active=True).first()
        if not tenant:
            raise CommandError(f"Tenant '{tc}' not found or inactive.")

        # Internal username: t{tenant_id}_{display}
        internal_uname = user_internal_username(tenant, display_uname)
        self.stdout.write(f"Display: {display_uname} -> Internal: {internal_uname}")

        deleted_bare_id = None
        with transaction.atomic():
            # The destructive cleanup and replacement/reset are one unit. A
            # later membership or password failure must restore the bare user.
            if options.get("cleanup_bare"):
                bare = User.objects.select_for_update().filter(username=display_uname).first()
                if bare and bare.username != internal_uname:
                    deleted_bare_id = bare.id
                    bare.delete()

            user = User.objects.select_for_update().filter(username=internal_uname).first()
            if not user:
                user = User(
                    username=internal_uname,
                    tenant=tenant,
                    is_active=True,
                    is_staff=True,
                    must_change_password=True,
                )
                if name:
                    user.name = name
                user.set_password(pw)
                user.save()
                core_repo.membership_ensure_active(tenant=tenant, user=user, role=role)
                self.stdout.write(self.style.SUCCESS(
                    f"CREATED user '{internal_uname}' on tenant '{tc}' (id={user.id})"
                ))
            else:
                user.is_active = True
                user.tenant = tenant
                fields = ["is_active", "tenant"]
                if name:
                    user.name = name
                    fields.append("name")
                user.save(update_fields=fields)

                from apps.core.services.password import force_reset_password

                force_reset_password(user, pw)
                mem = core_repo.membership_ensure_active(
                    tenant=tenant,
                    user=user,
                    role=role,
                )
                self.stdout.write(self.style.SUCCESS(
                    f"RESET password for '{internal_uname}' on tenant '{tc}' (id={user.id}, role={mem.role})"
                ))

        if deleted_bare_id is not None:
            self.stdout.write(
                self.style.WARNING(
                    f"Deleted bare user '{display_uname}' (id={deleted_bare_id})"
                )
            )
