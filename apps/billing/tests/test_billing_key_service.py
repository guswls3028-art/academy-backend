"""
billing_key_service 단위 테스트

TossPaymentsClient를 mock하여 서비스 로직만 검증한다.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from apps.billing.models import BillingKey, BillingProfile
from apps.billing.services import billing_key_service
from apps.core.models import Tenant


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

    # ------------------------------------------------------------------
    # issue_billing_key
    # ------------------------------------------------------------------

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_issue_billing_key_success(self, mock_get_client):
        """빌링키 발급 성공 시 BillingKey가 생성된다."""
        mock_client = MagicMock()
        mock_client.issue_billing_key.return_value = {
            "success": True,
            "billingKey": "bk_test_abc123",
            "customerKey": "cus_xxx",
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
