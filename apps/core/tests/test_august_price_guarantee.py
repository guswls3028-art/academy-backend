from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.billing.services import invoice_service
from apps.core.models import Program, Tenant


@override_settings(TIME_ZONE="Asia/Seoul")
class AugustPriceGuaranteeTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="August Price Academy",
            code="august-price-academy",
            is_active=True,
        )
        self.program = Program.objects.get(tenant=self.tenant)

    def _set_joined_at(self, joined_at: datetime, *, monthly_price: int = 145_000):
        Program.objects.filter(pk=self.program.pk).update(
            created_at=joined_at,
            monthly_price=monthly_price,
        )
        self.program.refresh_from_db()

    def test_promotion_date_includes_all_of_august_only(self):
        self.assertFalse(Program.is_august_2026_promotion_date(date(2026, 7, 31)))
        self.assertTrue(Program.is_august_2026_promotion_date(date(2026, 8, 1)))
        self.assertTrue(Program.is_august_2026_promotion_date(date(2026, 8, 31)))
        self.assertFalse(Program.is_august_2026_promotion_date(date(2026, 9, 1)))

    def test_joined_at_uses_korean_calendar_boundaries(self):
        self._set_joined_at(datetime(2026, 7, 31, 15, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(self.program.joined_on, date(2026, 8, 1))
        self.assertTrue(self.program.has_lifetime_price_guarantee)

        self._set_joined_at(
            datetime(2026, 8, 31, 14, 59, 59, tzinfo=dt_timezone.utc)
        )
        self.assertEqual(self.program.joined_on, date(2026, 8, 31))
        self.assertTrue(self.program.has_lifetime_price_guarantee)

        self._set_joined_at(datetime(2026, 8, 31, 15, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(self.program.joined_on, date(2026, 9, 1))
        self.assertFalse(self.program.has_lifetime_price_guarantee)

    def test_august_price_remains_fixed_after_future_list_price_increase(self):
        self._set_joined_at(datetime(2026, 8, 15, tzinfo=dt_timezone.utc))

        self.program.monthly_price = 180_000
        self.program.save(update_fields=["monthly_price"])
        self.program.refresh_from_db()

        self.assertEqual(self.program.monthly_price, 145_000)
        self.assertEqual(self.program.expected_monthly_price, 145_000)
        self.assertEqual(self.program.billing_price_policy, "promotion")
        self.assertEqual(
            self.program.price_guarantee_code,
            "august_2026_lifetime",
        )
        self.assertEqual(self.program.list_monthly_price, 180_000)
        self.assertEqual(
            self.program.list_monthly_amounts,
            {
                "supply_amount": 180_000,
                "tax_amount": 18_000,
                "total_amount": 198_000,
            },
        )
        self.assertEqual(self.program.monthly_discount_rate, 19)
        self.assertEqual(
            invoice_service.resolve_monthly_amounts(self.program),
            {
                "supply_amount": 145_000,
                "tax_amount": 14_000,
                "total_amount": 159_000,
            },
        )

    def test_existing_non_august_price_changes_only_after_explicit_migration(self):
        self._set_joined_at(datetime(2026, 9, 1, tzinfo=dt_timezone.utc))

        self.program.feature_flags = {"student_app_enabled": True}
        self.program.save(update_fields=["feature_flags"])
        self.program.refresh_from_db()

        self.assertEqual(self.program.monthly_price, 145_000)
        self.assertFalse(self.program.has_lifetime_price_guarantee)
        self.assertEqual(
            self.program.billing_price_integrity,
            "single_price_mismatch",
        )
        with self.assertRaises(invoice_service.BillingPriceIntegrityError):
            invoice_service.resolve_monthly_amounts(self.program)

        Program.objects.filter(pk=self.program.pk).update(monthly_price=180_000)
        self.program.refresh_from_db()

        self.assertEqual(self.program.billing_price_integrity, "ok")
        self.assertEqual(
            invoice_service.resolve_monthly_amounts(self.program),
            {
                "supply_amount": 180_000,
                "tax_amount": 18_000,
                "total_amount": 198_000,
            },
        )

    def test_pre_price_change_program_keeps_legacy_contract(self):
        self._set_joined_at(datetime(2026, 7, 25, tzinfo=dt_timezone.utc))

        self.program.display_name = "Legacy Contract Academy"
        self.program.save(update_fields=["display_name"])
        self.program.refresh_from_db()

        self.assertEqual(self.program.monthly_price, 145_000)
        self.assertEqual(self.program.expected_monthly_price, 145_000)
        self.assertEqual(self.program.billing_price_integrity, "ok")

    @patch(
        "apps.core.models.program.timezone.localdate",
        return_value=date(2026, 9, 1),
    )
    def test_new_non_august_signup_starts_at_future_standard_price(self, _localdate):
        tenant = Tenant.objects.create(
            name="Future Price Academy",
            code="future-price-academy",
            is_active=True,
        )

        program = Program.objects.get(tenant=tenant)
        self.assertEqual(program.monthly_price, 180_000)
        self.assertEqual(
            program.monthly_amounts,
            {
                "supply_amount": 180_000,
                "tax_amount": 18_000,
                "total_amount": 198_000,
            },
        )
