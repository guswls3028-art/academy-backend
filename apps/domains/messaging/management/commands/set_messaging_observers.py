from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Read or replace one tenant's audited Alimtalk observer users."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--user-id", type=int, action="append", default=[])
        parser.add_argument("--clear", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--ack-sensitive-content", action="store_true")

    def handle(self, *args, **options):
        from apps.core.models import Tenant, TenantMembership
        from apps.domains.messaging.models import MessagingObserver
        from apps.domains.messaging.observers import ALLOWED_OBSERVER_ROLES

        tenant_id = options["tenant_id"]
        requested_ids = options["user_id"]
        clear = options["clear"]
        apply = options["apply"]

        if tenant_id <= 0:
            raise CommandError("tenant-id must be positive")
        if clear and requested_ids:
            raise CommandError("--clear and --user-id cannot be combined")
        if not clear and not requested_ids:
            raise CommandError("provide at least one --user-id or use --clear")
        if len(requested_ids) != len(set(requested_ids)):
            raise CommandError("duplicate --user-id values are not allowed")
        if apply and not clear and not options["ack_sensitive_content"]:
            raise CommandError("--ack-sensitive-content is required when enabling observers")

        tenant = Tenant.objects.filter(pk=tenant_id, is_active=True).first()
        if tenant is None:
            raise CommandError("active tenant not found")

        selected_ids = [] if clear else sorted(requested_ids)
        memberships = list(
            TenantMembership.objects.select_related("user")
            .filter(
                tenant=tenant,
                is_active=True,
                user_id__in=selected_ids,
                user__is_active=True,
                role__in=ALLOWED_OBSERVER_ROLES,
            )
            .order_by("user_id")
        )
        if len(memberships) != len(selected_ids):
            found_ids = {membership.user_id for membership in memberships}
            raise CommandError(
                "users must be active owner/admin/staff tenant members; "
                f"invalid_user_ids={sorted(set(selected_ids) - found_ids)}"
            )
        invalid_phone_ids = [
            membership.user_id
            for membership in memberships
            if not (
                len(phone := "".join(c for c in str(membership.user.phone or "") if c.isdigit())) == 11
                and phone.startswith("010")
            )
        ]
        if invalid_phone_ids:
            raise CommandError(f"observer users need valid phones; invalid_user_ids={invalid_phone_ids}")

        current_ids = list(
            MessagingObserver.objects.filter(tenant=tenant)
            .order_by("user_id")
            .values_list("user_id", flat=True)
        )
        self.stdout.write(
            f"tenant_id={tenant_id} current_user_ids={current_ids} requested_user_ids={selected_ids} apply={apply}"
        )
        if not apply:
            self.stdout.write(self.style.WARNING("dry-run: no rows changed"))
            return

        with transaction.atomic():
            MessagingObserver.objects.filter(tenant=tenant).exclude(
                user_id__in=selected_ids
            ).delete()
            for user_id in selected_ids:
                MessagingObserver.objects.get_or_create(
                    tenant=tenant,
                    user_id=user_id,
                )

        final_ids = list(
            MessagingObserver.objects.filter(tenant=tenant)
            .order_by("user_id")
            .values_list("user_id", flat=True)
        )
        if final_ids != selected_ids:
            raise CommandError("observer post-write readback mismatch")
        self.stdout.write(
            self.style.SUCCESS(
                f"messaging_observers_updated tenant_id={tenant_id} user_ids={final_ids}"
            )
        )
