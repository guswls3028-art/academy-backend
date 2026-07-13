"""
402 미들웨어 테스트.

검증 항목:
- active → 통과
- grace → 통과
- expired → 402
- cancel_at_period_end=True + active → 통과 (해지 예약 중이어도 서비스 이용 가능)
- billing 경로 면제
"""

import json
from datetime import date, timedelta

from django.test import RequestFactory, TestCase, override_settings

from apps.core.middleware.tenant import _check_subscription, _is_subscription_exempt_path
from apps.core.models.program import Program
from apps.core.models.tenant import Tenant


@override_settings(BILLING_EXEMPT_TENANT_IDS=set())
class TestSubscriptionCheck(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="테스트학원", code="test_402", is_active=True
        )
        self.program = Program.objects.get(tenant=self.tenant)
        self.factory = RequestFactory()

    def _check(self):
        # tenant.program 캐시를 무효화하기 위해 새로 로드
        tenant = Tenant.objects.get(pk=self.tenant.pk)
        request = self.factory.get("/api/v1/lectures/")
        return _check_subscription(tenant, request)

    def test_active_passes(self):
        self.program.subscription_status = "active"
        self.program.subscription_expires_at = date.today() + timedelta(days=30)
        self.program.save()
        self.assertIsNone(self._check())

    def test_grace_passes(self):
        self.program.subscription_status = "grace"
        self.program.subscription_expires_at = date.today() + timedelta(days=5)
        self.program.save()
        self.assertIsNone(self._check())

    @override_settings(BILLING_GRACE_PERIOD_DAYS=7)
    def test_grace_passes_after_paid_period_end_until_grace_end(self):
        self.program.subscription_status = "grace"
        self.program.subscription_expires_at = date.today() - timedelta(days=6)
        self.program.save()

        self.assertIsNone(self._check())

    @override_settings(BILLING_GRACE_PERIOD_DAYS=7)
    def test_grace_returns_402_after_grace_end(self):
        self.program.subscription_status = "grace"
        self.program.subscription_expires_at = date.today() - timedelta(days=8)
        self.program.save()

        response = self._check()

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 402)
        payload = json.loads(response.content)
        self.assertEqual(
            payload["subscription_expires_at"],
            str(self.program.subscription_expires_at),
        )
        self.assertEqual(
            payload["grace_expires_at"],
            str(self.program.subscription_expires_at + timedelta(days=7)),
        )
        self.assertEqual(payload["expires_at"], payload["grace_expires_at"])

    def test_expired_returns_402(self):
        self.program.subscription_status = "expired"
        self.program.subscription_expires_at = date.today() - timedelta(days=1)
        self.program.save()
        response = self._check()
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 402)

    def test_active_past_expires_returns_402(self):
        """active이지만 만료일이 지난 경우 → is_subscription_active=False → 402"""
        self.program.subscription_status = "active"
        self.program.subscription_expires_at = date.today() - timedelta(days=1)
        self.program.save()
        response = self._check()
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 402)

    def test_cancel_at_period_end_still_active(self):
        """해지 예약 중이지만 아직 기간 남음 → 통과"""
        self.program.subscription_status = "active"
        self.program.subscription_expires_at = date.today() + timedelta(days=15)
        self.program.cancel_at_period_end = True
        self.program.save()
        self.assertIsNone(self._check())

    def test_no_expires_fails_closed(self):
        """비면제 테넌트의 만료일 누락은 무제한 이용으로 해석하지 않는다."""
        self.program.subscription_status = "active"
        self.program.subscription_expires_at = None
        self.program.save()
        response = self._check()
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 402)

    def test_missing_program_returns_configuration_error(self):
        self.program.delete()

        response = self._check()

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            json.loads(response.content)["code"],
            "subscription_configuration_error",
        )


class TestExemptPaths(TestCase):

    def test_billing_path_exempt(self):
        self.assertTrue(_is_subscription_exempt_path("/api/v1/billing/invoices/"))

    def test_billing_admin_exempt(self):
        self.assertTrue(_is_subscription_exempt_path("/api/v1/billing/admin/dashboard/"))

    def test_core_subscription_exempt(self):
        self.assertTrue(_is_subscription_exempt_path("/api/v1/core/subscription/"))

    def test_lectures_not_exempt(self):
        self.assertFalse(_is_subscription_exempt_path("/api/v1/lectures/"))
