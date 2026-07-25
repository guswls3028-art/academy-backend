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

        with patch.dict(
            Program.PLAN_PRICES,
            {Program.Plan.ALL: 198_000},
            clear=True,
        ):
            self.program.monthly_price = 198_000
            self.program.save(update_fields=["monthly_price"])
            self.program.refresh_from_db()

            self.assertEqual(self.program.monthly_price, 145_000)
            self.assertEqual(self.program.expected_monthly_price, 145_000)
            self.assertEqual(self.program.billing_price_policy, "promotion")
            self.assertEqual(
                self.program.price_guarantee_code,
                "august_2026_lifetime",
            )
            self.assertEqual(
                invoice_service.resolve_monthly_amounts(self.program),
                {
                    "supply_amount": 145_000,
                    "tax_amount": 14_000,
                    "total_amount": 159_000,
                },
            )

    def test_non_august_signup_tracks_future_standard_price(self):
        self._set_joined_at(datetime(2026, 9, 1, tzinfo=dt_timezone.utc))

        with patch.dict(
            Program.PLAN_PRICES,
            {Program.Plan.ALL: 198_000},
            clear=True,
        ):
            self.program.save(update_fields=["monthly_price"])
            self.program.refresh_from_db()

            self.assertEqual(self.program.monthly_price, 198_000)
            self.assertFalse(self.program.has_lifetime_price_guarantee)
            self.assertEqual(
                invoice_service.resolve_monthly_amounts(self.program),
                {
                    "supply_amount": 198_000,
                    "tax_amount": 19_800,
                    "total_amount": 217_800,
                },
            )
