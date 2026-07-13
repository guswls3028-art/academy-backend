"""Safely re-wrap stored provider billing keys after dedicated KEK rotation."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.billing.models import BillingKey
from apps.billing.services.billing_key_crypto import (
    BillingKeyCryptoError,
    decrypt_billing_key,
    reencrypt_billing_key,
)


class Command(BaseCommand):
    help = "Re-encrypt BillingKey credentials with the current dedicated KEK."

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Apply the re-encryption; default is a read-only preview.",
        )
        parser.add_argument(
            "--confirm-live",
            action="store_true",
            help="Required with --execute because this updates payment credentials.",
        )

    def handle(self, *args, **options):
        if not settings.BILLING_KEY_ENCRYPTION_WRITE_ENABLED:
            raise CommandError(
                "BILLING_KEY_ENCRYPTION_WRITE_ENABLED must be true before rotation."
            )
        if options["execute"] and not options["confirm_live"]:
            raise CommandError("--execute requires --confirm-live.")

        rows = list(BillingKey.objects.order_by("id").only("id", "billing_key"))
        invalid_ids: list[int] = []
        for row in rows:
            try:
                decrypt_billing_key(row.billing_key)
            except BillingKeyCryptoError:
                invalid_ids.append(row.id)
        if invalid_ids:
            raise CommandError(
                "billing key rotation preflight failed: "
                f"undecryptable_count={len(invalid_ids)} "
                f"sample_ids={invalid_ids[:20]}"
            )

        if not options["execute"]:
            self.stdout.write(
                f"billing_key_rotation_preview total={len(rows)} "
                f"would_reencrypt={len(rows)}"
            )
            return

        with transaction.atomic():
            locked_rows = list(
                BillingKey.objects.select_for_update()
                .order_by("id")
                .only("id", "billing_key")
            )
            for row in locked_rows:
                row.billing_key = reencrypt_billing_key(row.billing_key)
            if locked_rows:
                BillingKey.objects.bulk_update(
                    locked_rows,
                    ["billing_key"],
                    batch_size=500,
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"billing_key_rotation_complete reencrypted={len(locked_rows)}"
            )
        )
