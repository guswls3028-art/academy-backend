from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.billing.adapters.toss_payments import TossPaymentsClient
from apps.billing.models import PaymentTransaction
from apps.billing.services.webhook_service import handle_payment_status


class Command(BaseCommand):
    help = "Query Toss by orderId and reconcile provider-outcome-unknown transactions"

    def add_arguments(self, parser):
        parser.add_argument("--transaction-id", action="append", type=int, dest="tx_ids")
        parser.add_argument("--min-age-minutes", type=int, default=15)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(minutes=max(0, options["min_age_minutes"]))
        queryset = PaymentTransaction.objects.filter(
            Q(
                status="PROCESSING",
                processing_started_at__lte=cutoff,
            )
            | Q(
                status="PARTIALLY_REFUNDED",
                updated_at__lte=cutoff,
            )
        ).order_by("id")
        if options.get("tx_ids"):
            queryset = queryset.filter(id__in=options["tx_ids"])

        transactions = list(queryset.select_related("invoice", "tenant"))
        if options["dry_run"]:
            for tx in transactions:
                self.stdout.write(
                    f"would_reconcile tenant={tx.tenant.code} tx={tx.id} "
                    f"invoice={tx.invoice.invoice_number}"
                )
            return

        client = TossPaymentsClient()
        reconciled = 0
        unresolved = 0
        for tx in transactions:
            payment = client.get_payment_by_order_id(tx.provider_order_id)
            if not payment.get("success"):
                unresolved += 1
                self.stderr.write(
                    f"query_failed tenant={tx.tenant.code} tx={tx.id} "
                    f"invoice={tx.invoice.invoice_number} "
                    f"code={payment.get('error_code', '')}"
                )
                continue
            result = handle_payment_status(payment)
            if result.get("result") in {
                "applied_done",
                "already_success",
                "repaired_success_invoice",
                "applied_failed",
                "already_failed",
                "applied_canceled",
                "already_refunded",
                "applied_full_refund",
            }:
                reconciled += 1
            else:
                unresolved += 1
            self.stdout.write(
                f"tenant={tx.tenant.code} tx={tx.id} "
                f"invoice={tx.invoice.invoice_number} result={result.get('result')}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"reconcile complete candidates={len(transactions)} "
                f"reconciled={reconciled} unresolved={unresolved}"
            )
        )
        if unresolved:
            raise CommandError(
                f"payment_reconciliation_unresolved:{unresolved}"
            )
