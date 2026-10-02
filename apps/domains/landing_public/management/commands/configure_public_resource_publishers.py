from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Tenant
from apps.core.services.tenant_access import get_authorized_tenant_role
from apps.domains.landing_public.models.resource import PublicResourceBoardAccess


class Command(BaseCommand):
    help = "Validate two exact resource publishers; write only with --apply."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-code", required=True)
        parser.add_argument("--publisher-ids", type=int, nargs=2, required=True)
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(code=options["tenant_code"], is_active=True).first()
        if tenant is None:
            raise CommandError("Exact active tenant not found.")
        first, second = options["publisher_ids"]
        if first == second:
            raise CommandError("Two distinct publisher IDs are required.")
        users = list(get_user_model().objects.select_for_update().filter(pk__in=[first, second], is_active=True))
        if len(users) != 2 or any(
            get_authorized_tenant_role(user, tenant) not in ("owner", "admin", "teacher") for user in users
        ):
            raise CommandError("Both publishers must be active staff members of the exact tenant.")
        if options["apply"]:
            PublicResourceBoardAccess.objects.update_or_create(
                tenant=tenant, defaults={"publisher_one_id": first, "publisher_two_id": second}
            )
        self.stdout.write(f"tenant={tenant.pk} publisher_ids={first},{second} applied={bool(options['apply'])}")
