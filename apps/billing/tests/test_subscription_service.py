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

from django.test import TestCase
from django.utils import timezone

from apps.billing.models import Invoice, PaymentTransaction
from apps.billing.services import subscription_service
from apps.billing.services.subscription_service import SubscriptionTransitionError
from apps.core.models.program import Program
from apps.core.models.tenant import Tenant


class SubscriptionServiceTestBase(TestCase):
    """공통 fixture"""

    def setUp(self):
        # 기본 설정의 exempt id(1, 2)를 먼저 소비해 상태 전이 테스트 대상은 live tenant가 되게 한다.
        Tenant.objects.create(name="시스템 테넌트", code="billing_exempt_1", is_active=True)
        Tenant.objects.create(name="테스트 테넌트", code="billing_exempt_2", is_active=True)
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

    def test_renew_preserves_cancel_flag_until_explicit_revoke(self):
        self.program.cancel_at_period_end = True
        self.program.canceled_at = timezone.now()
        self.program.save(update_fields=["cancel_at_period_end", "canceled_at"])

        new_expires = date.today() + timedelta(days=30)
        result = subscription_service.renew(self.program.pk, new_expires)
        self.assertTrue(result.cancel_at_period_end)
        self.assertIsNotNone(result.canceled_at)


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

    def test_cancel_fails_closed_when_expiry_is_missing(self):
        self.program.subscription_expires_at = None
        self.program.save(update_fields=["subscription_expires_at"])

        with self.assertRaisesRegex(
            SubscriptionTransitionError,
            "subscription_expires_at is not configured",
        ):
            subscription_service.schedule_cancel(self.program.pk)

        self.program.refresh_from_db()
        self.assertFalse(self.program.cancel_at_period_end)

    def test_revoke_cancel(self):
        subscription_service.schedule_cancel(self.program.pk)
        result = subscription_service.revoke_cancel(self.program.pk)
        self.assertFalse(result.cancel_at_period_end)
        self.assertIsNone(result.canceled_at)

    def test_revoke_cancel_restores_only_cancel_voided_future_invoice(self):
        cancel_invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-REVOKE-CANCEL-VOID",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=self.program.subscription_expires_at + timedelta(days=1),
            period_end=self.program.subscription_expires_at + timedelta(days=30),
            due_date=self.program.subscription_expires_at + timedelta(days=1),
            status="SCHEDULED",
        )
        ordinary_void = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-REVOKE-ORDINARY-VOID",
            plan="pro",
            billing_mode="INVOICE_REQUEST",
            supply_amount=1,
            tax_amount=0,
            total_amount=1,
            period_start=self.program.subscription_expires_at + timedelta(days=31),
            period_end=self.program.subscription_expires_at + timedelta(days=60),
            due_date=self.program.subscription_expires_at + timedelta(days=31),
            status="VOID",
            memo="manual_admin_void",
        )

        subscription_service.schedule_cancel(self.program.pk)
        subscription_service.revoke_cancel(self.program.pk)

        cancel_invoice.refresh_from_db()
        ordinary_void.refresh_from_db()
        self.assertEqual(cancel_invoice.status, "SCHEDULED")
        self.assertEqual(cancel_invoice.memo, "")
        self.assertEqual(ordinary_void.status, "VOID")
        self.assertEqual(ordinary_void.memo, "manual_admin_void")

    def test_cancel_during_grace_expires_immediately(self):
        """유료 기간이 끝난 grace에서는 기간말 해지를 즉시 만료 처리한다."""
        self.program.subscription_status = "grace"
        self.program.save(update_fields=["subscription_status"])
        result = subscription_service.schedule_cancel(self.program.pk)
        self.assertTrue(result.cancel_at_period_end)
        self.assertEqual(result.subscription_status, "expired")

    def test_revoke_cancel_rejects_grace_and_expired(self):
        for subscription_status in ("grace", "expired"):
            with self.subTest(subscription_status=subscription_status):
                self.program.subscription_status = subscription_status
                self.program.cancel_at_period_end = True
                self.program.canceled_at = timezone.now()
                self.program.save(
                    update_fields=[
                        "subscription_status",
                        "cancel_at_period_end",
                        "canceled_at",
                    ]
                )

                with self.assertRaisesRegex(
                    SubscriptionTransitionError,
                    "unless subscription is active",
                ):
                    subscription_service.revoke_cancel(self.program.pk)

                self.program.refresh_from_db()
                self.assertTrue(self.program.cancel_at_period_end)

    def test_schedule_cancel_voids_future_unpaid_invoice(self):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-CANCEL-FUTURE",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=self.program.subscription_expires_at + timedelta(days=1),
            period_end=self.program.subscription_expires_at + timedelta(days=30),
            due_date=self.program.subscription_expires_at + timedelta(days=1),
            status="SCHEDULED",
        )

        subscription_service.schedule_cancel(self.program.pk)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "VOID")
        self.assertEqual(invoice.memo, "cancel_at_period_end")

    def test_schedule_cancel_rejects_inflight_future_payment(self):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-CANCEL-PROCESSING",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=self.program.subscription_expires_at + timedelta(days=1),
            period_end=self.program.subscription_expires_at + timedelta(days=30),
            due_date=self.program.subscription_expires_at + timedelta(days=1),
            status="PENDING",
        )
        PaymentTransaction.objects.create(
            tenant=self.tenant,
            invoice=invoice,
            provider="tosspayments",
            provider_order_id=invoice.provider_order_id,
            idempotency_key=invoice.provider_order_id,
            amount=invoice.total_amount,
            status="PROCESSING",
        )

        with self.assertRaisesRegex(SubscriptionTransitionError, "payment is being processed"):
            subscription_service.schedule_cancel(self.program.pk)

        self.program.refresh_from_db()
        invoice.refresh_from_db()
        self.assertFalse(self.program.cancel_at_period_end)
        self.assertEqual(invoice.status, "PENDING")

    def test_schedule_cancel_rejects_captured_unapplied_future_payment(self):
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-CANCEL-CAPTURED",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=self.program.subscription_expires_at + timedelta(days=1),
            period_end=self.program.subscription_expires_at + timedelta(days=30),
            due_date=self.program.subscription_expires_at + timedelta(days=1),
            status="PENDING",
        )
        PaymentTransaction.objects.create(
            tenant=self.tenant,
            invoice=invoice,
            provider="tosspayments",
            provider_order_id=invoice.provider_order_id,
            idempotency_key=invoice.provider_order_id,
            amount=invoice.total_amount,
            status="SUCCESS",
        )

        with self.assertRaisesRegex(
            SubscriptionTransitionError,
            "captured renewal payment requires reconciliation",
        ):
            subscription_service.schedule_cancel(self.program.pk)

        self.program.refresh_from_db()
        invoice.refresh_from_db()
        self.assertFalse(self.program.cancel_at_period_end)
        self.assertEqual(invoice.status, "PENDING")


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

    def test_change_plan_overwrites_manual_price_override(self):
        self.program.monthly_price = 150_000
        self.program.save(update_fields=["monthly_price"])

        result = subscription_service.change_plan(self.program.pk, "max")

        self.assertEqual(result.plan, "max")
        self.assertEqual(result.monthly_price, 330_000)

    def test_program_save_applies_contract_price_override(self):
        tenant = Tenant.objects.create(
            name="Ymath", code="ymath", is_active=True
        )
        program = Program.objects.get(tenant=tenant)
        program.plan = "pro"
        program.monthly_price = 198_000
        program.save(update_fields=["plan", "monthly_price"])

        program.refresh_from_db()

        self.assertEqual(program.plan, "pro")
        self.assertEqual(program.monthly_price, 150_000)

    def test_change_plan_applies_contract_price_override(self):
        tenant = Tenant.objects.create(
            name="Limglish", code="limglish", is_active=True
        )
        program = Program.objects.get(tenant=tenant)
        program.plan = "pro"
        program.monthly_price = 198_000
        program.save(update_fields=["plan", "monthly_price"])

        result = subscription_service.change_plan(program.pk, "max")

        self.assertEqual(result.plan, "max")
        self.assertEqual(result.monthly_price, 150_000)

    def test_invalid_plan_raises(self):
        with self.assertRaises(ValueError):
            subscription_service.change_plan(self.program.pk, "enterprise")
