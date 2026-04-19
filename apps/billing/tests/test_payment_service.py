"""
payment_service (Phase D: 자동결제 실행) 단위 테스트.

TossPaymentsClient를 mock하여 결제 로직/멱등성/상태 전이를 검증.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing.models import BillingKey, BillingProfile, Invoice, PaymentTransaction
from apps.billing.services import payment_service
from apps.core.models import Tenant
from apps.core.models.program import Program


@override_settings(
    TOSS_AUTO_BILLING_ENABLED=True,
    TOSS_PAYMENTS_SECRET_KEY="test_sk_dummy",
    BILLING_EXEMPT_TENANT_IDS=set(),
)
class PaymentServiceTestBase(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="결제학원", code="pay_test", is_active=True)
        self.program = Program.objects.get(tenant=self.tenant)
        self.program.subscription_status = "active"
        self.program.subscription_expires_at = date.today() + timedelta(days=5)
        self.program.plan = "pro"
        self.program.monthly_price = 198_000
        self.program.billing_mode = "AUTO_CARD"
        self.program.save()

        self.profile = BillingProfile.objects.create(
            tenant=self.tenant,
            payer_name="임근혁",
            payer_email="test@limglish.kr",
        )
        self.billing_key = BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=self.profile,
            billing_key="bk_test_xyz",
            card_company="삼성",
            card_number_masked="**** **** **** 1234",
            is_active=True,
        )

        self.invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-TEST-001",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=date.today(),
            period_end=date.today() + timedelta(days=30),
            due_date=date.today(),
            status="SCHEDULED",
        )


@override_settings(TOSS_AUTO_BILLING_ENABLED=True, BILLING_EXEMPT_TENANT_IDS=set())
class TestExecuteAutoPaymentSuccess(PaymentServiceTestBase):

    @patch("apps.billing.services.payment_service._get_client")
    def test_success_flow(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.charge_with_billing_key.return_value = {
            "success": True,
            "paymentKey": "pay_mock_abc",
            "status": "DONE",
            "approvedAt": "2026-04-20T12:34:56+09:00",
            "card": {"company": "삼성", "number": "**** **** **** 1234"},
        }
        mock_get_client.return_value = mock_client

        result = payment_service.execute_auto_payment(self.invoice.pk)

        self.assertTrue(result["success"])
        self.assertEqual(result["payment_key"], "pay_mock_abc")

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PAID")
        self.assertIsNotNone(self.invoice.paid_at)

        tx = PaymentTransaction.objects.get(pk=result["tx_id"])
        self.assertEqual(tx.status, "SUCCESS")
        self.assertEqual(tx.provider_payment_key, "pay_mock_abc")
        self.assertEqual(tx.idempotency_key, self.invoice.provider_order_id)

        self.program.refresh_from_db()
        self.assertEqual(self.program.subscription_status, "active")
        self.assertEqual(self.program.subscription_expires_at, self.invoice.period_end)

    @patch("apps.billing.services.payment_service._get_client")
    def test_idempotency_second_call_no_double_charge(self, mock_get_client):
        """동일 invoice로 두 번 호출해도 Toss API는 한 번만 호출되고 tx도 1개"""
        mock_client = MagicMock()
        mock_client.charge_with_billing_key.return_value = {
            "success": True,
            "paymentKey": "pay_mock_abc",
        }
        mock_get_client.return_value = mock_client

        r1 = payment_service.execute_auto_payment(self.invoice.pk)
        r2 = payment_service.execute_auto_payment(self.invoice.pk)

        self.assertTrue(r1["success"])
        self.assertTrue(r2["success"])
        self.assertEqual(r1["tx_id"], r2["tx_id"])
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        # Toss API는 한 번만 호출
        self.assertEqual(mock_client.charge_with_billing_key.call_count, 1)


@override_settings(TOSS_AUTO_BILLING_ENABLED=True, BILLING_EXEMPT_TENANT_IDS=set())
class TestExecuteAutoPaymentFailure(PaymentServiceTestBase):

    @patch("apps.billing.services.payment_service._get_client")
    def test_toss_failure_marks_invoice_failed(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.charge_with_billing_key.return_value = {
            "success": False,
            "error_code": "INVALID_CARD",
            "error_message": "카드 인증 실패",
        }
        mock_get_client.return_value = mock_client

        result = payment_service.execute_auto_payment(self.invoice.pk)

        self.assertFalse(result["success"])
        self.assertIn("INVALID_CARD", result["reason"])

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "FAILED")
        self.assertEqual(self.invoice.attempt_count, 1)
        self.assertIsNotNone(self.invoice.next_retry_at)

        tx = PaymentTransaction.objects.get(pk=result["tx_id"])
        self.assertEqual(tx.status, "FAILED")
        self.assertIn("INVALID_CARD", tx.failure_reason)

    def test_no_billing_key_marks_invoice_failed_without_api_call(self):
        self.billing_key.is_active = False
        self.billing_key.save()

        with patch("apps.billing.services.payment_service._get_client") as mock_gc:
            result = payment_service.execute_auto_payment(self.invoice.pk)
            mock_gc.assert_not_called()

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "no_active_billing_key")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "FAILED")

    def test_exempt_tenant_skipped(self):
        with self.settings(BILLING_EXEMPT_TENANT_IDS={self.tenant.id}):
            with patch("apps.billing.services.payment_service._get_client") as mock_gc:
                result = payment_service.execute_auto_payment(self.invoice.pk)
                mock_gc.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "exempt_tenant")


@override_settings(BILLING_EXEMPT_TENANT_IDS=set())
class TestExecuteAutoPaymentGuards(PaymentServiceTestBase):

    @override_settings(TOSS_AUTO_BILLING_ENABLED=False)
    def test_auto_billing_disabled_skips_call(self):
        with patch("apps.billing.services.payment_service._get_client") as mock_gc:
            result = payment_service.execute_auto_payment(self.invoice.pk)
            mock_gc.assert_not_called()
        self.assertFalse(result["success"])
        self.assertIn("TOSS_AUTO_BILLING_ENABLED", result["reason"])

    @override_settings(TOSS_AUTO_BILLING_ENABLED=True)
    def test_already_paid_invoice_is_idempotent_success(self):
        """이미 PAID인 인보이스는 Toss 호출 없이 success 반환 (멱등성)"""
        self.invoice.status = "PAID"
        self.invoice.save()
        with patch("apps.billing.services.payment_service._get_client") as mock_gc:
            result = payment_service.execute_auto_payment(self.invoice.pk)
            mock_gc.assert_not_called()
        self.assertTrue(result["success"])
        self.assertEqual(result["reason"], "already_paid")

    @override_settings(TOSS_AUTO_BILLING_ENABLED=True)
    def test_invoice_request_mode_rejected(self):
        self.invoice.billing_mode = "INVOICE_REQUEST"
        self.invoice.save()
        with patch("apps.billing.services.payment_service._get_client") as mock_gc:
            result = payment_service.execute_auto_payment(self.invoice.pk)
            mock_gc.assert_not_called()
        self.assertFalse(result["success"])
        self.assertIn("INVOICE_REQUEST", result["reason"])

    @override_settings(TOSS_AUTO_BILLING_ENABLED=True)
    @patch("apps.billing.services.payment_service._get_client")
    def test_failed_invoice_retried_successfully(self, mock_get_client):
        """FAILED → PENDING → 결제 성공"""
        self.invoice.status = "FAILED"
        self.invoice.attempt_count = 1
        self.invoice.next_retry_at = date.today()
        self.invoice.save()

        mock_client = MagicMock()
        mock_client.charge_with_billing_key.return_value = {
            "success": True, "paymentKey": "pay_retry_ok",
        }
        mock_get_client.return_value = mock_client

        result = payment_service.execute_auto_payment(self.invoice.pk)
        self.assertTrue(result["success"])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PAID")
