from datetime import date
from importlib import import_module

from django.apps import apps
from django.test import TestCase

from apps.billing.models import Invoice
from apps.core.models import Tenant
from apps.core.models.program import Program


single_plan_migration = import_module(
    "apps.core.migrations.0046_apply_single_subscription_plan"
)


class SinglePlanMigrationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="single-plan-migration",
            name="Single Plan Migration",
            is_active=True,
        )
        Program.objects.filter(tenant=self.tenant).update(
            plan="pro",
            monthly_price=198_000,
        )

    def _invoice(self, *, suffix: str, status: str, month: int) -> Invoice:
        return Invoice.objects.create(
            tenant=self.tenant,
            invoice_number=f"INV-MIGRATION-{suffix}",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=date(2026, month, 1),
            period_end=date(2026, month, 28),
            due_date=date(2026, month, 5),
            status=status,
        )

    def test_converges_program_and_only_unissued_scheduled_invoice(self):
        scheduled = self._invoice(
            suffix="SCHEDULED",
            status="SCHEDULED",
            month=8,
        )
        pending = self._invoice(suffix="PENDING", status="PENDING", month=9)

        single_plan_migration.apply_single_subscription_plan(apps, None)

        program = Program.objects.get(tenant=self.tenant)
        self.assertEqual(program.plan, "all")
        self.assertEqual(program.monthly_price, 145_000)

        scheduled.refresh_from_db()
        self.assertEqual(scheduled.plan, "all")
        self.assertEqual(scheduled.supply_amount, 145_000)
        self.assertEqual(scheduled.tax_amount, 14_000)
        self.assertEqual(scheduled.total_amount, 159_000)

        pending.refresh_from_db()
        self.assertEqual(pending.plan, "pro")
        self.assertEqual(pending.supply_amount, 198_000)
        self.assertEqual(pending.tax_amount, 19_800)
        self.assertEqual(pending.total_amount, 217_800)

    def test_data_migration_is_intentionally_irreversible(self):
        operation = single_plan_migration.Migration.operations[0]

        self.assertFalse(operation.reversible)
