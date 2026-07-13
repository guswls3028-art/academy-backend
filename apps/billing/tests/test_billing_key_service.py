"""
billing_key_service 단위 테스트

TossPaymentsClient를 mock하여 서비스 로직만 검증한다.
"""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.billing.models import BillingKey, BillingProfile, Invoice, PaymentTransaction
from apps.billing.services import billing_key_service
from apps.core.models import OpsAuditLog, Tenant

TEST_BILLING_KEK = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


class BillingKeyServiceTestCase(TestCase):
    """billing_key_service 테스트"""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="test_billing",
            name="Test Billing Academy",
        )

    # ------------------------------------------------------------------
    # get_or_create_customer_key
    # ------------------------------------------------------------------

    def test_get_or_create_customer_key_creates_profile(self):
        """BillingProfile이 없으면 새로 생성하고 customer_key를 반환한다."""
        self.assertFalse(
            BillingProfile.objects.filter(tenant=self.tenant).exists()
        )

        customer_key = billing_key_service.get_or_create_customer_key(self.tenant.id)

        self.assertTrue(customer_key.startswith("cus_"))
        profile = BillingProfile.objects.get(tenant=self.tenant)
        self.assertEqual(profile.provider_customer_key, customer_key)

    def test_get_or_create_customer_key_returns_existing(self):
        """이미 BillingProfile이 있으면 기존 customer_key를 반환한다."""
        profile = BillingProfile.objects.create(
            tenant=self.tenant, provider="tosspayments"
        )
        existing_key = profile.provider_customer_key

        customer_key = billing_key_service.get_or_create_customer_key(self.tenant.id)

        self.assertEqual(customer_key, existing_key)

    @override_settings(
        BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY=TEST_BILLING_KEK,
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
    )
    def test_audit_reports_plaintext_key_after_encrypted_writes_enabled(self):
        profile = BillingProfile.objects.create(
            tenant=self.tenant,
            provider="tosspayments",
        )
        with override_settings(BILLING_KEY_ENCRYPTION_WRITE_ENABLED=False):
            billing_key = BillingKey.objects.create(
                tenant=self.tenant,
                billing_profile=profile,
                billing_key="legacy-plaintext-provider-token",
            )
        output = StringIO()

        call_command(
            "audit_billing_fields",
            tenant=self.tenant.code,
            stdout=output,
        )

        audit = output.getvalue()
        self.assertIn(f"plaintext_billing_key id={billing_key.id}", audit)
        self.assertNotIn("legacy-plaintext-provider-token", audit)

    # ------------------------------------------------------------------
    # issue_billing_key
    # ------------------------------------------------------------------

    def _processing_payment(self) -> PaymentTransaction:
        invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number=f"INV-CARD-MUTATION-{PaymentTransaction.objects.count() + 1}",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=100_000,
            tax_amount=10_000,
            total_amount=110_000,
            period_start=timezone.localdate(),
            period_end=timezone.localdate(),
            due_date=timezone.localdate(),
            status="PENDING",
        )
        return PaymentTransaction.objects.create(
            tenant=self.tenant,
            invoice=invoice,
            provider="tosspayments",
            provider_order_id=invoice.provider_order_id,
            idempotency_key=invoice.provider_order_id,
            amount=invoice.total_amount,
            status="PROCESSING",
            processing_started_at=timezone.now(),
        )

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_issue_billing_key_success(self, mock_get_client):
        """빌링키 발급 성공 시 BillingKey가 생성된다."""
        mock_client = MagicMock()
        mock_client.issue_billing_key.side_effect = lambda *, auth_key, customer_key: {
            "success": True,
            "billingKey": "bk_test_abc123",
            "customerKey": customer_key,
            "card": {
                "company": "삼성",
                "number": "**** **** **** 1234",
            },
        }
        mock_get_client.return_value = mock_client

        bk = billing_key_service.issue_billing_key(self.tenant.id, "auth_key_test")

        self.assertIsInstance(bk, BillingKey)
        self.assertEqual(bk.billing_key, "bk_test_abc123")
        self.assertEqual(bk.card_company, "삼성")
        self.assertEqual(bk.card_number_masked, "**** **** **** 1234")
        self.assertTrue(bk.is_active)
        self.assertEqual(bk.tenant_id, self.tenant.id)

        # BillingProfile도 생성되었는지 확인
        self.assertTrue(
            BillingProfile.objects.filter(tenant=self.tenant).exists()
        )

    @override_settings(
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY=TEST_BILLING_KEK,
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
        BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
    )
    @patch("apps.billing.services.billing_key_service._get_client")
    def test_issue_encrypts_at_rest_and_delete_decrypts_for_provider(
        self,
        mock_get_client,
    ):
        mock_client = MagicMock()
        mock_client.issue_billing_key.side_effect = (
            lambda *, auth_key, customer_key: {
                "success": True,
                "billingKey": "bk_sensitive_provider_token",
                "customerKey": customer_key,
                "card": {"company": "삼성", "number": "**** 1234"},
            }
        )
        mock_client.delete_billing_key.return_value = {"success": True}
        mock_get_client.return_value = mock_client

        billing_key = billing_key_service.issue_billing_key(
            self.tenant.id,
            "auth_key_test",
        )
        billing_key.refresh_from_db()

        self.assertTrue(billing_key.billing_key.startswith("enc:v1:"))
        self.assertNotIn("bk_sensitive_provider_token", billing_key.billing_key)
        self.assertTrue(billing_key_service.delete_billing_key(billing_key.id))
        mock_client.delete_billing_key.assert_called_once_with(
            "bk_sensitive_provider_token"
        )

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_issue_deactivates_old_key(self, mock_get_client):
        """새 빌링키 발급 시 기존 활성 키가 비활성화된다."""
        # 기존 활성 키 생성
        profile = BillingProfile.objects.create(
            tenant=self.tenant, provider="tosspayments"
        )
        old_key = BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=profile,
            billing_key="bk_old_key",
            card_company="현대",
            card_number_masked="**** 5678",
            is_active=True,
        )

        mock_client = MagicMock()
        mock_client.issue_billing_key.return_value = {
            "success": True,
            "billingKey": "bk_new_key",
            "customerKey": profile.provider_customer_key,
            "card": {
                "company": "삼성",
                "number": "**** 1234",
            },
        }
        mock_get_client.return_value = mock_client

        new_key = billing_key_service.issue_billing_key(self.tenant.id, "auth_new")

        # 새 키는 활성
        self.assertTrue(new_key.is_active)
        self.assertEqual(new_key.billing_key, "bk_new_key")

        # 기존 키는 비활성
        old_key.refresh_from_db()
        self.assertFalse(old_key.is_active)
        self.assertIsNotNone(old_key.deactivated_at)

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_issue_billing_key_toss_failure(self, mock_get_client):
        """Toss API 실패 시 ValueError가 발생한다."""
        mock_client = MagicMock()
        mock_client.issue_billing_key.return_value = {
            "success": False,
            "error_code": "INVALID_AUTH_KEY",
            "error_message": "유효하지 않은 인증키입니다.",
        }
        mock_get_client.return_value = mock_client

        with self.assertRaises(ValueError) as ctx:
            billing_key_service.issue_billing_key(self.tenant.id, "bad_auth_key")

        self.assertIn("INVALID_AUTH_KEY", str(ctx.exception))

        # BillingKey가 생성되지 않았는지 확인
        self.assertEqual(
            BillingKey.objects.filter(tenant=self.tenant).count(), 0
        )

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_issue_blocks_before_provider_call_during_processing_payment(self, mock_get_client):
        self._processing_payment()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        with self.assertRaisesRegex(ValueError, "결제 결과를 확인 중"):
            billing_key_service.issue_billing_key(self.tenant.id, "auth_key")

        mock_client.issue_billing_key.assert_not_called()

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_issue_transport_unknown_preserves_local_key_state(self, mock_get_client):
        mock_get_client.return_value.issue_billing_key.return_value = {
            "success": False,
            "error_code": "TIMEOUT",
            "outcome_unknown": True,
        }

        with self.assertRaises(billing_key_service.BillingProviderOutcomeUnknown):
            billing_key_service.issue_billing_key(self.tenant.id, "auth_unknown")

        self.assertFalse(BillingKey.objects.filter(tenant=self.tenant).exists())

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_issue_provider_success_then_db_failure_requires_reconciliation(
        self,
        mock_get_client,
    ):
        profile = BillingProfile.objects.create(
            tenant=self.tenant,
            provider="tosspayments",
        )
        mock_get_client.return_value.issue_billing_key.return_value = {
            "success": True,
            "billingKey": "bk_provider_applied",
            "customerKey": profile.provider_customer_key,
            "card": {"company": "삼성", "number": "**** 4321"},
        }

        with patch.object(
            BillingKey.objects,
            "create",
            side_effect=IntegrityError("local write failed"),
        ):
            with self.assertRaises(
                billing_key_service.BillingProviderOutcomeUnknown
            ):
                billing_key_service.issue_billing_key(
                    self.tenant.id,
                    "auth_provider_applied",
                )

        self.assertFalse(BillingKey.objects.filter(tenant=self.tenant).exists())
        audit = OpsAuditLog.objects.get(
            action="billing.card_reconciliation_required"
        )
        self.assertEqual(audit.target_tenant_id, self.tenant.id)
        self.assertEqual(audit.payload["operation"], "issue")
        self.assertNotIn("billingKey", audit.payload)

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_issue_rejects_mismatched_customer_key_before_local_mutation(
        self,
        mock_get_client,
    ):
        mock_get_client.return_value.issue_billing_key.return_value = {
            "success": True,
            "billingKey": "bk_wrong_customer",
            "customerKey": "cus_other_tenant",
            "card": {"company": "삼성", "number": "**** 4321"},
        }

        with self.assertRaises(billing_key_service.BillingProviderOutcomeUnknown):
            billing_key_service.issue_billing_key(
                self.tenant.id,
                "auth_wrong_customer",
            )

        self.assertFalse(BillingKey.objects.filter(tenant=self.tenant).exists())

    # ------------------------------------------------------------------
    # delete_billing_key
    # ------------------------------------------------------------------

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_delete_billing_key_success(self, mock_get_client):
        """빌링키 삭제 성공 시 로컬도 비활성화된다."""
        profile = BillingProfile.objects.create(
            tenant=self.tenant, provider="tosspayments"
        )
        bk = BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=profile,
            billing_key="bk_to_delete",
            is_active=True,
        )

        mock_client = MagicMock()
        mock_client.delete_billing_key.return_value = {"success": True}
        mock_get_client.return_value = mock_client

        result = billing_key_service.delete_billing_key(bk.id)

        self.assertTrue(result)
        bk.refresh_from_db()
        self.assertFalse(bk.is_active)
        self.assertIsNotNone(bk.deactivated_at)

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_delete_billing_key_toss_failure_keeps_local(self, mock_get_client):
        """Toss API 삭제 실패 시 로컬 빌링키는 활성 상태를 유지한다."""
        profile = BillingProfile.objects.create(
            tenant=self.tenant, provider="tosspayments"
        )
        bk = BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=profile,
            billing_key="bk_keep_active",
            is_active=True,
        )

        mock_client = MagicMock()
        mock_client.delete_billing_key.return_value = {
            "success": False,
            "error_code": "BILLING_KEY_NOT_FOUND",
            "error_message": "빌링키를 찾을 수 없습니다.",
        }
        mock_get_client.return_value = mock_client

        result = billing_key_service.delete_billing_key(bk.id)

        self.assertFalse(result)
        bk.refresh_from_db()
        self.assertTrue(bk.is_active)
        self.assertIsNone(bk.deactivated_at)

    def test_delete_billing_key_not_found(self):
        """존재하지 않는 빌링키 삭제 시 False를 반환한다."""
        result = billing_key_service.delete_billing_key(99999)
        self.assertFalse(result)

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_delete_blocks_before_provider_call_during_processing_payment(self, mock_get_client):
        profile = BillingProfile.objects.create(
            tenant=self.tenant,
            provider="tosspayments",
        )
        bk = BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=profile,
            billing_key="bk_processing_guard",
            is_active=True,
        )
        self._processing_payment()
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        with self.assertRaisesRegex(ValueError, "결제 결과를 확인 중"):
            billing_key_service.delete_billing_key(bk.id)

        mock_client.delete_billing_key.assert_not_called()
        bk.refresh_from_db()
        self.assertTrue(bk.is_active)

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_delete_transport_unknown_keeps_local_key_active(self, mock_get_client):
        profile = BillingProfile.objects.create(
            tenant=self.tenant,
            provider="tosspayments",
        )
        bk = BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=profile,
            billing_key="bk_delete_unknown",
            is_active=True,
        )
        mock_get_client.return_value.delete_billing_key.return_value = {
            "success": False,
            "error_code": "CONNECTION_ERROR",
            "outcome_unknown": True,
        }

        with self.assertRaises(billing_key_service.BillingProviderOutcomeUnknown):
            billing_key_service.delete_billing_key(bk.id)

        bk.refresh_from_db()
        self.assertTrue(bk.is_active)

    @override_settings(
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY=TEST_BILLING_KEK,
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS=(),
        BILLING_KEY_ENCRYPTION_WRITE_ENABLED=True,
    )
    @patch("apps.billing.services.billing_key_service._get_client")
    def test_delete_undecryptable_key_is_local_security_failure(
        self,
        mock_get_client,
    ):
        profile = BillingProfile.objects.create(
            tenant=self.tenant,
            provider="tosspayments",
        )
        bk = BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=profile,
            billing_key="provider-key-before-corruption",
            is_active=True,
        )
        BillingKey.objects.filter(pk=bk.pk).update(
            billing_key="enc:v1:corrupt-ciphertext"
        )
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        with self.assertRaises(billing_key_service.BillingCredentialUnavailable):
            billing_key_service.delete_billing_key(bk.id)

        mock_client.delete_billing_key.assert_not_called()
        bk.refresh_from_db()
        self.assertTrue(bk.is_active)
        self.assertFalse(
            OpsAuditLog.objects.filter(
                action="billing.card_reconciliation_required",
                target_tenant=self.tenant,
            ).exists()
        )
        security_audit = OpsAuditLog.objects.get(
            action="billing.card_configuration_failed",
            target_tenant=self.tenant,
        )
        self.assertEqual(security_audit.payload["stage"], "decrypt")

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_delete_provider_success_then_db_failure_requires_reconciliation(
        self,
        mock_get_client,
    ):
        profile = BillingProfile.objects.create(
            tenant=self.tenant,
            provider="tosspayments",
        )
        bk = BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=profile,
            billing_key="bk_delete_provider_applied",
            is_active=True,
        )
        mock_get_client.return_value.delete_billing_key.return_value = {
            "success": True,
        }

        with patch.object(
            BillingKey,
            "save",
            side_effect=RuntimeError("local deactivate failed"),
        ):
            with self.assertRaises(
                billing_key_service.BillingProviderOutcomeUnknown
            ):
                billing_key_service.delete_billing_key(bk.id)

        bk.refresh_from_db()
        self.assertTrue(bk.is_active)
        audit = OpsAuditLog.objects.get(
            action="billing.card_reconciliation_required"
        )
        self.assertEqual(audit.payload["operation"], "delete")
        self.assertEqual(audit.payload["billing_key_id"], bk.id)
        self.assertNotIn("billing_key", audit.payload)

    # ------------------------------------------------------------------
    # get_active_billing_key / list_billing_keys
    # ------------------------------------------------------------------

    def test_get_active_billing_key_returns_active(self):
        """활성 빌링키가 있으면 반환한다."""
        profile = BillingProfile.objects.create(
            tenant=self.tenant, provider="tosspayments"
        )
        bk = BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=profile,
            billing_key="bk_active",
            is_active=True,
        )

        result = billing_key_service.get_active_billing_key(self.tenant.id)
        self.assertEqual(result.id, bk.id)

    def test_get_active_billing_key_returns_none(self):
        """활성 빌링키가 없으면 None을 반환한다."""
        result = billing_key_service.get_active_billing_key(self.tenant.id)
        self.assertIsNone(result)

    def test_list_billing_keys(self):
        """테넌트의 모든 빌링키를 반환한다."""
        profile = BillingProfile.objects.create(
            tenant=self.tenant, provider="tosspayments"
        )
        BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=profile,
            billing_key="bk_1",
            is_active=False,
            deactivated_at=timezone.now(),
        )
        BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=profile,
            billing_key="bk_2",
            is_active=True,
        )

        keys = billing_key_service.list_billing_keys(self.tenant.id)
        self.assertEqual(keys.count(), 2)
