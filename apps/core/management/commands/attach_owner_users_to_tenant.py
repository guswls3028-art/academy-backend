# PATH: apps/core/management/commands/attach_owner_users_to_tenant.py
"""
TenantMembership(role=owner) 있는 User를 해당 테넌트에 붙이고 username을 t{tenant_id}_{display} 로 정규화.
운영 DB에서 오너는 있는데 User.tenant가 비어 있거나 레거시 username인 경우 1회 실행.

사용: python manage.py attach_owner_users_to_tenant --apply
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model

from apps.core.models import TenantMembership
from apps.core.models.user import user_internal_username, user_display_username, USERNAME_TENANT_PREFIX

User = get_user_model()


class Command(BaseCommand):
    help = "Attach owner users to their tenant and normalize username to t{id}_{display}."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply changes.")

    def handle(self, *args, **options):
        apply = options.get("apply", False)
        if not apply:
            self.stdout.write(self.style.WARNING("Dry-run. Use --apply to save."))

        qs = (
            TenantMembership.objects.filter(role="owner", is_active=True)
            .select_related("tenant", "user")
        )
        updated = 0
        skipped = 0
        errors = []

        for m in qs:
            user = m.user
            tenant = m.tenant
            if not user:
                continue
            display = user_display_username(user) or (user.username or "").strip()
            if not display:
                continue
            prefix = f"{USERNAME_TENANT_PREFIX}{tenant.id}_"
            internal = user_internal_username(tenant, display)

            if user.tenant_id == tenant.id and (user.username or "").startswith(prefix):
                skipped += 1
                continue

            if User.objects.filter(username=internal).exclude(pk=user.pk).exists():
                errors.append(f"user_id={user.pk} conflict: {internal!r} exists")
                continue

            if apply:
                with transaction.atomic():
                    user.tenant = tenant
                    user.username = internal
                    user.save(update_fields=["tenant_id", "username"])
                self.stdout.write(f"  user_id={user.pk} -> tenant={tenant.code} username={internal!r}")
                updated += 1
            else:
                self.stdout.write(f"  [would] user_id={user.pk} -> tenant={tenant.code} username={internal!r}")
                updated += 1

        for e in errors:
            self.stderr.write(self.style.ERROR(e))
        self.stdout.write(
            self.style.SUCCESS(f"Done. updated={updated} skipped={skipped} errors={len(errors)}" + (" (applied)" if apply else " (dry-run)"))
        )
