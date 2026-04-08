"""
SubscriptionService 단위 테스트.

테스트 범위:
- 상태 전이 유효성 (active↔grace↔expired)
- cancel_at_period_end 동작 (해지 예약/철회)
- 수동 연장
- 플랜 변경
- 잘못된 전이 차단
- exempt 테넌트 제외
"""

from datetime import date, timedelta

from django.test import TestCase, override_settings

from apps.billing.services import subscription_service
from apps.billing.services.subscription_service import SubscriptionTransitionError
from apps.core.models.program import Program
from apps.core.models.tenant import Tenant


class SubscriptionServiceTestBase(TestCase):
    """공통 fixture"""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="테스트학원", code="test_billing", is_active=True
        )
        # signal이 Program을 자동 생성하므로 가져와서 구독 필드 설정
        self.program = Program.objects.get(tenant=self.tenant)
        self.program.subscription_status = "active"
        self.program.subscription_started_at = date.today() - timedelta(days=30)
        self.program.subscription_expires_at = date.today() + timedelta(days=30)
        self.program.plan = "pro"
        self.program.monthly_price = 198_000
        self.program.save()


class TestRenew(SubscriptionServiceTestBase):

    def test_renew_from_active(self):
        new_expires = date.today() + timedelta(days=60)
        result = subscription_service.renew(self.program.pk, new_expires)
        self.assertEqual(result.subscription_status, "active")
        self.assertEqual(result.subscription_expires_at, new_expires)
        self.assertFalse(result.cancel_at_period_end)

    def test_renew_from_expired(self):
        self.program.subscription_status = "expired"
        self.program.save(update_fields=["subscription_status"])

        new_expires = date.today() + timedelta(days=30)
        result = subscription_service.renew(self.program.pk, new_expires)
        self.assertEqual(result.subscription_status, "active")

    def test_renew_from_grace(self):
        self.program.subscription_status = "grace"
        self.program.save(update_fields=["subscription_status"])

        new_expires = date.today() + timedelta(days=30)
        result = subscription_service.renew(self.program.pk, new_expires)
        self.assertEqual(result.subscription_status, "active")

    def test_renew_clears_cancel_flag(self):
        self.program.cancel_at_period_end = True
        self.program.save(update_fields=["cancel_at_period_end"])

        new_expires = date.today() + timedelta(days=30)
        result = subscription_service.renew(self.program.pk, new_expires)
        self.assertFalse(result.cancel_at_period_end)
        self.assertIsNone(result.canceled_at)


class TestGrace(SubscriptionServiceTestBase):

    def test_active_to_grace(self):
        result = subscription_service.enter_grace(self.program.pk)
        self.assertEqual(result.subscription_status, "grace")

    def test_grace_from_expired_raises(self):
        self.program.subscription_status = "expired"
        self.program.save(update_fields=["subscription_status"])
        with self.assertRaises(SubscriptionTransitionError):
            subscription_service.enter_grace(self.program.pk)

    def test_grace_from_grace_raises(self):
        self.program.subscription_status = "grace"
        self.program.save(update_fields=["subscription_status"])
        with self.assertRaises(SubscriptionTransitionError):
            subscription_service.enter_grace(self.program.pk)

    def test_exempt_tenant_skips_grace(self):
        """exempt 테넌트는 grace 전이 안 됨"""
        with self.settings(BILLING_EXEMPT_TENANT_IDS={self.tenant.id}):
            result = subscription_service.enter_grace(self.program.pk)
            self.assertEqual(result.subscription_status, "active")


class TestExpire(SubscriptionServiceTestBase):

    def test_grace_to_expired(self):
        self.program.subscription_status = "grace"
        self.program.save(update_fields=["subscription_status"])
        result = subscription_service.expire(self.program.pk)
        self.assertEqual(result.subscription_status, "expired")

    def test_active_to_expired(self):
        result = subscription_service.expire(self.program.pk)
        self.assertEqual(result.subscription_status, "expired")

    def test_expired_to_expired_raises(self):
        self.program.subscription_status = "expired"
        self.program.save(update_fields=["subscription_status"])
        with self.assertRaises(SubscriptionTransitionError):
            subscription_service.expire(self.program.pk)


class TestCancelSchedule(SubscriptionServiceTestBase):

    def test_schedule_cancel(self):
        result = subscription_service.schedule_cancel(self.program.pk)
        self.assertTrue(result.cancel_at_period_end)
        self.assertIsNotNone(result.canceled_at)
        # 상태는 변하지 않아야 함!
        self.assertEqual(result.subscription_status, "active")

    def test_cancel_from_expired_raises(self):
        self.program.subscription_status = "expired"
        self.program.save(update_fields=["subscription_status"])
        with self.assertRaises(SubscriptionTransitionError):
            subscription_service.schedule_cancel(self.program.pk)

    def test_revoke_cancel(self):
        subscription_service.schedule_cancel(self.program.pk)
        result = subscription_service.revoke_cancel(self.program.pk)
        self.assertFalse(result.cancel_at_period_end)
        self.assertIsNone(result.canceled_at)

    def test_cancel_during_grace(self):
        """grace 상태에서도 해지 예약 가능"""
        self.program.subscription_status = "grace"
        self.program.save(update_fields=["subscription_status"])
        result = subscription_service.schedule_cancel(self.program.pk)
        self.assertTrue(result.cancel_at_period_end)
        self.assertEqual(result.subscription_status, "grace")


class TestExtend(SubscriptionServiceTestBase):

    def test_extend_active(self):
        old_expires = self.program.subscription_expires_at
        result = subscription_service.extend(self.program.pk, 30)
        self.assertEqual(result.subscription_expires_at, old_expires + timedelta(days=30))
        self.assertEqual(result.subscription_status, "active")

    def test_extend_expired_restores_active(self):
        self.program.subscription_status = "expired"
        self.program.subscription_expires_at = date.today() - timedelta(days=10)
        self.program.save(update_fields=["subscription_status", "subscription_expires_at"])

        result = subscription_service.extend(self.program.pk, 30)
        self.assertEqual(result.subscription_status, "active")
        # 만료일이 과거이므로 오늘 기준으로 연장
        self.assertEqual(result.subscription_expires_at, date.today() + timedelta(days=30))

    def test_extend_clears_cancel(self):
        self.program.cancel_at_period_end = True
        self.program.save(update_fields=["cancel_at_period_end"])
        result = subscription_service.extend(self.program.pk, 30)
        self.assertFalse(result.cancel_at_period_end)


class TestChangePlan(SubscriptionServiceTestBase):

    def test_change_plan(self):
        result = subscription_service.change_plan(self.program.pk, "max")
        self.assertEqual(result.plan, "max")
        self.assertEqual(result.monthly_price, 330_000)

    def test_invalid_plan_raises(self):
        with self.assertRaises(ValueError):
            subscription_service.change_plan(self.program.pk, "enterprise")
