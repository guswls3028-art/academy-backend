"""
payment_service (Phase D: 자동결제 실행) 단위 테스트.

TossPaymentsClient를 mock하여 결제 로직/멱등성/상태 전이를 검증.
"""

from datetime import date, timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase, override_settings

from apps.billing.models import BillingKey, BillingProfile, Invoice, PaymentTransaction
from apps.billing.services import payment_service
from apps.billing.services.billing_key_crypto import encrypt_billing_key
from apps.core.models import OpsAuditLog, Tenant
from apps.core.models.program import Program

TEST_BILLING_KEK = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


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
            "type": "BILLING",
            "paymentKey": "pay_mock_abc",
            "orderId": self.invoice.provider_order_id,
            "totalAmount": self.invoice.total_amount,
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

    @override_settings(
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY=TEST_BILLING_KEK,
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
        BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
    )
    @patch("apps.billing.services.payment_service._get_client")
    def test_encrypted_key_is_decrypted_only_at_provider_boundary(
        self,
        mock_get_client,
    ):
        self.billing_key.billing_key = encrypt_billing_key("bk_plain_for_provider")
        self.billing_key.save(update_fields=["billing_key", "updated_at"])
        mock_client = MagicMock()
        mock_client.charge_with_billing_key.return_value = {
            "success": True,
            "type": "BILLING",
            "paymentKey": "pay_encrypted_key",
            "orderId": self.invoice.provider_order_id,
            "totalAmount": self.invoice.total_amount,
            "status": "DONE",
        }
        mock_get_client.return_value = mock_client

        result = payment_service.execute_auto_payment(self.invoice.pk)

        self.assertTrue(result["success"])
        self.assertEqual(
            mock_client.charge_with_billing_key.call_args.kwargs["billing_key"],
            "bk_plain_for_provider",
        )

    @patch("apps.billing.services.payment_service._get_client")
    def test_idempotency_second_call_no_double_charge(self, mock_get_client):
        """동일 invoice로 두 번 호출해도 Toss API는 한 번만 호출되고 tx도 1개"""
        mock_client = MagicMock()
        mock_client.charge_with_billing_key.return_value = {
            "success": True,
            "type": "BILLING",
            "paymentKey": "pay_mock_abc",
            "orderId": self.invoice.provider_order_id,
            "totalAmount": self.invoice.total_amount,
            "status": "DONE",
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
    def test_success_response_with_wrong_amount_stays_processing_for_reconcile(
        self,
        mock_get_client,
    ):
        mock_get_client.return_value.charge_with_billing_key.return_value = {
            "success": True,
            "type": "BILLING",
            "paymentKey": "pay_wrong_amount",
            "orderId": self.invoice.provider_order_id,
            "totalAmount": self.invoice.total_amount + 1,
            "status": "DONE",
        }

        result = payment_service.execute_auto_payment(self.invoice.pk)

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "provider_response_mismatch")
        tx = PaymentTransaction.objects.get(pk=result["tx_id"])
        self.invoice.refresh_from_db()
        self.assertEqual(tx.status, "PROCESSING")
        self.assertEqual(self.invoice.status, "PENDING")

    @patch("apps.billing.services.payment_service._get_client")
    def test_transport_failure_result_stays_processing_for_reconcile(
        self,
        mock_get_client,
    ):
        mock_get_client.return_value.charge_with_billing_key.return_value = {
            "success": False,
            "error_code": "TIMEOUT",
            "error_message": "timed out",
            "outcome_unknown": True,
        }

        result = payment_service.execute_auto_payment(self.invoice.pk)

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "provider_outcome_unknown")
        self.assertTrue(result["reconciliation_required"])
        tx = PaymentTransaction.objects.get(pk=result["tx_id"])
        self.invoice.refresh_from_db()
        self.assertEqual(tx.status, "PROCESSING")
        self.assertEqual(self.invoice.status, "PENDING")

    @patch("apps.billing.services.payment_service._get_client")
    def test_duplicate_order_error_stays_processing_for_order_query(
        self,
        mock_get_client,
    ):
        mock_get_client.return_value.charge_with_billing_key.return_value = {
            "success": False,
            "error_code": "DUPLICATED_ORDER_ID",
            "error_message": "already approved or canceled",
            # Defense in depth: retain ambiguous classification even if a
            # custom adapter omitted the explicit outcome_unknown flag.
        }

        result = payment_service.execute_auto_payment(self.invoice.pk)

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "provider_outcome_unknown")
        self.assertTrue(result["reconciliation_required"])
        tx = PaymentTransaction.objects.get(pk=result["tx_id"])
        self.invoice.refresh_from_db()
        self.assertEqual(tx.status, "PROCESSING")
        self.assertEqual(self.invoice.status, "PENDING")

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

    def test_pending_transaction_unique_race_requeries_after_savepoint_rollback(self):
        existing = PaymentTransaction.objects.create(
            tenant=self.tenant,
            invoice=self.invoice,
            provider="tosspayments",
            payment_method="card",
            provider_order_id=self.invoice.provider_order_id,
            idempotency_key=self.invoice.provider_order_id,
            amount=self.invoice.total_amount,
            status="PENDING",
        )
        original_filter = PaymentTransaction.objects.filter
        first_lookup = MagicMock()
        first_lookup.order_by.return_value.first.return_value = None
        lookup_count = 0

        def hide_existing_once(*args, **kwargs):
            nonlocal lookup_count
            lookup_count += 1
            if lookup_count == 1:
                return first_lookup
            return original_filter(*args, **kwargs)

        with patch.object(
            PaymentTransaction.objects,
            "filter",
            side_effect=hide_existing_once,
        ):
            result = payment_service._create_pending_tx(
                self.invoice,
                self.billing_key,
            )

        self.assertEqual(result.id, existing.id)
        self.assertEqual(PaymentTransaction.objects.count(), 1)

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
            "success": True,
            "type": "BILLING",
            "paymentKey": "pay_retry_ok",
            "orderId": self.invoice.provider_order_id,
            "totalAmount": self.invoice.total_amount,
            "status": "DONE",
        }
        mock_get_client.return_value = mock_client

        result = payment_service.execute_auto_payment(self.invoice.pk)
        self.assertTrue(result["success"])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PAID")

    @override_settings(TOSS_AUTO_BILLING_ENABLED=True)
    def test_processing_transaction_blocks_second_provider_call(self):
        self.invoice.status = "PENDING"
        self.invoice.save(update_fields=["status"])
        tx = PaymentTransaction.objects.create(
            tenant=self.tenant,
            invoice=self.invoice,
            provider="tosspayments",
            payment_method="card",
            provider_order_id=self.invoice.provider_order_id,
            idempotency_key=self.invoice.provider_order_id,
            amount=self.invoice.total_amount,
            status="PROCESSING",
        )

        with patch("apps.billing.services.payment_service._get_client") as mock_client:
            result = payment_service.execute_auto_payment(self.invoice.pk)

        mock_client.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "payment_in_progress")
        self.assertEqual(result["tx_id"], tx.id)

    @override_settings(TOSS_AUTO_BILLING_ENABLED=True)
    @patch("apps.billing.services.payment_service._get_client")
    def test_unknown_provider_exception_leaves_durable_processing_claim(self, mock_get_client):
        mock_get_client.return_value.charge_with_billing_key.side_effect = TimeoutError(
            "provider outcome unknown"
        )

        result = payment_service.execute_auto_payment(self.invoice.pk)

        tx = PaymentTransaction.objects.get(invoice=self.invoice)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "provider_outcome_unknown")
        self.assertTrue(result["reconciliation_required"])
        self.assertEqual(tx.status, "PROCESSING")
        self.assertIsNotNone(tx.processing_started_at)

        audit = StringIO()
        call_command(
            "audit_billing_fields",
            tenant=self.tenant.code,
            stdout=audit,
        )
        self.assertIn("payment_processing_unresolved", audit.getvalue())
        self.assertIn(f"tx={tx.id}", audit.getvalue())

        mock_get_client.reset_mock()
        result = payment_service.execute_auto_payment(self.invoice.pk)
        self.assertEqual(result["reason"], "payment_in_progress")
        mock_get_client.assert_not_called()

    @override_settings(TOSS_AUTO_BILLING_ENABLED=True)
    @patch(
        "apps.billing.services.payment_service._get_client",
        side_effect=RuntimeError("invalid payment client config"),
    )
    def test_client_setup_failure_does_not_consume_customer_retry_budget(self, _mock_get_client):
        result = payment_service.execute_auto_payment(self.invoice.pk)

        self.assertEqual(result["reason"], "payment_configuration_unavailable")
        tx = PaymentTransaction.objects.get(invoice=self.invoice)
        self.assertEqual(tx.status, "PENDING")
        self.assertIsNone(tx.processing_started_at)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PENDING")
        self.assertEqual(self.invoice.attempt_count, 0)
        self.assertTrue(
            OpsAuditLog.objects.filter(
                action="billing.payment_configuration_failed",
                target_tenant=self.tenant,
            ).exists()
        )

    @override_settings(
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY=TEST_BILLING_KEK,
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
        BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
    )
    @patch("apps.billing.services.payment_service._get_client")
    def test_undecryptable_key_fails_before_tx_and_preserves_retry_budget(
        self,
        mock_get_client,
    ):
        BillingKey.objects.filter(pk=self.billing_key.pk).update(
            billing_key="enc:v1:corrupt-ciphertext"
        )

        result = payment_service.execute_auto_payment(self.invoice.pk)

        self.assertEqual(result["reason"], "billing_credential_unavailable")
        self.assertIsNone(result["tx_id"])
        mock_get_client.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PENDING")
        self.assertEqual(self.invoice.attempt_count, 0)
        audit = OpsAuditLog.objects.get(
            action="billing.payment_configuration_failed",
            target_tenant=self.tenant,
        )
        self.assertEqual(audit.payload["stage"], "decrypt")

    @override_settings(TOSS_AUTO_BILLING_ENABLED=True)
    def test_cancelled_future_period_is_voided_without_provider_call(self):
        self.program.cancel_at_period_end = True
        self.program.save(update_fields=["cancel_at_period_end"])
        self.billing_key.is_active = False
        self.billing_key.save(update_fields=["is_active"])
        self.invoice.period_start = self.program.subscription_expires_at + timedelta(days=1)
        self.invoice.period_end = self.invoice.period_start + timedelta(days=29)
        self.invoice.due_date = self.invoice.period_start
        self.invoice.save(update_fields=["period_start", "period_end", "due_date"])

        with patch("apps.billing.services.payment_service._get_client") as mock_client:
            result = payment_service.execute_auto_payment(self.invoice.pk)

        mock_client.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "cancel_at_period_end")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "VOID")
