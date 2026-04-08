"""
카드 등록 API 통합 테스트.

엔드포인트:
- POST /api/v1/billing/card/register/prepare/
- POST /api/v1/billing/card/register/callback/
- DELETE /api/v1/billing/cards/{pk}/
- GET /api/v1/billing/cards/

권한: TenantResolvedAndOwner
"""

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.billing.models import BillingKey, BillingProfile
from apps.core.models import Tenant, TenantMembership
from apps.core.models.program import Program

User = get_user_model()

MOCK_ISSUE_RESPONSE = {
    "success": True,
    "billingKey": "bk_test_1234567890",
    "customerKey": "cus_test_abc",
    "card": {
        "company": "삼성",
        "number": "**** **** **** 1234",
    },
}

MOCK_DELETE_RESPONSE = {"success": True}
MOCK_DELETE_FAIL = {"success": False, "error_code": "NOT_FOUND", "error_message": "Billing key not found"}


class CardApiTestBase(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Academy", code="card_test", is_active=True)
        self.program = Program.objects.get(tenant=self.tenant)
        self.program.subscription_status = "active"
        self.program.subscription_expires_at = date.today() + timedelta(days=30)
        self.program.save()

        self.owner = User.objects.create(
            username=f"t{self.tenant.id}_owner", tenant=self.tenant,
            is_active=True, is_staff=True, name="Owner",
        )
        self.owner.set_password("test1234!")
        self.owner.save(update_fields=["password"])
        TenantMembership.objects.create(
            user=self.owner, tenant=self.tenant, role="owner", is_active=True,
        )

        self.staff = User.objects.create(
            username=f"t{self.tenant.id}_staff", tenant=self.tenant,
            is_active=True, is_staff=True, name="Staff",
        )
        self.staff.set_password("test1234!")
        self.staff.save(update_fields=["password"])
        TenantMembership.objects.create(
            user=self.staff, tenant=self.tenant, role="staff", is_active=True,
        )

        self.headers = {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant.code}


class TestCardRegisterPrepare(CardApiTestBase):
    def test_owner_gets_prepare_data(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post("/api/v1/billing/card/register/prepare/", **self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("clientKey", resp.data)
        self.assertIn("customerKey", resp.data)
        self.assertIn("successUrl", resp.data)
        self.assertIn("failUrl", resp.data)
        # customerKey는 UUID 형식
        self.assertTrue(resp.data["customerKey"].startswith("cus_"))

    def test_staff_cannot_prepare(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.post("/api/v1/billing/card/register/prepare/", **self.headers)
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_rejected(self):
        resp = self.client.post("/api/v1/billing/card/register/prepare/", **self.headers)
        self.assertIn(resp.status_code, [401, 403])


class TestCardRegisterCallback(CardApiTestBase):
    @patch("apps.billing.services.billing_key_service._get_client")
    def test_callback_issues_billing_key(self, mock_client_fn):
        mock_client = mock_client_fn.return_value
        mock_client.issue_billing_key.return_value = MOCK_ISSUE_RESPONSE

        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            "/api/v1/billing/card/register/callback/",
            {"authKey": "test_auth_key_123"},
            format="json", **self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["card_company"], "삼성")
        self.assertIn("1234", resp.data["card_number_masked"])
        self.assertTrue(resp.data["is_active"])

        # DB 확인
        bk = BillingKey.objects.get(tenant=self.tenant, is_active=True)
        self.assertEqual(bk.billing_key, "bk_test_1234567890")

    def test_callback_without_authkey_400(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            "/api/v1/billing/card/register/callback/",
            {}, format="json", **self.headers,
        )
        self.assertEqual(resp.status_code, 400)

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_callback_toss_failure_400(self, mock_client_fn):
        mock_client = mock_client_fn.return_value
        mock_client.issue_billing_key.return_value = {
            "success": False, "error_code": "INVALID_AUTH_KEY", "error_message": "Invalid auth key",
        }

        self.client.force_authenticate(user=self.owner)
        resp = self.client.post(
            "/api/v1/billing/card/register/callback/",
            {"authKey": "bad_key"},
            format="json", **self.headers,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("INVALID_AUTH_KEY", resp.data["detail"])


class TestCardDelete(CardApiTestBase):
    def _create_billing_key(self):
        profile, _ = BillingProfile.objects.get_or_create(tenant=self.tenant)
        return BillingKey.objects.create(
            tenant=self.tenant, billing_profile=profile,
            provider="tosspayments", billing_key="bk_del_test",
            card_company="현대", card_number_masked="**** 5678",
            is_active=True,
        )

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_owner_can_delete_card(self, mock_client_fn):
        mock_client = mock_client_fn.return_value
        mock_client.delete_billing_key.return_value = MOCK_DELETE_RESPONSE

        bk = self._create_billing_key()
        self.client.force_authenticate(user=self.owner)
        resp = self.client.delete(f"/api/v1/billing/cards/{bk.pk}/", **self.headers)
        self.assertEqual(resp.status_code, 200)

        bk.refresh_from_db()
        self.assertFalse(bk.is_active)

    @patch("apps.billing.services.billing_key_service._get_client")
    def test_toss_delete_failure_keeps_card_active(self, mock_client_fn):
        mock_client = mock_client_fn.return_value
        mock_client.delete_billing_key.return_value = MOCK_DELETE_FAIL

        bk = self._create_billing_key()
        self.client.force_authenticate(user=self.owner)
        resp = self.client.delete(f"/api/v1/billing/cards/{bk.pk}/", **self.headers)
        self.assertEqual(resp.status_code, 502)

        bk.refresh_from_db()
        self.assertTrue(bk.is_active)  # 로컬 보호!

    def test_staff_cannot_delete(self):
        bk = self._create_billing_key()
        self.client.force_authenticate(user=self.staff)
        resp = self.client.delete(f"/api/v1/billing/cards/{bk.pk}/", **self.headers)
        self.assertEqual(resp.status_code, 403)

    def test_nonexistent_card_404(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.delete("/api/v1/billing/cards/99999/", **self.headers)
        self.assertEqual(resp.status_code, 404)


class TestCardList(CardApiTestBase):
    def test_owner_lists_active_cards(self):
        profile, _ = BillingProfile.objects.get_or_create(tenant=self.tenant)
        BillingKey.objects.create(
            tenant=self.tenant, billing_profile=profile,
            provider="tosspayments", billing_key="bk_list_1",
            card_company="삼성", card_number_masked="**** 1111",
            is_active=True,
        )
        BillingKey.objects.create(
            tenant=self.tenant, billing_profile=profile,
            provider="tosspayments", billing_key="bk_list_2",
            card_company="현대", card_number_masked="**** 2222",
            is_active=False,  # 비활성 — 안 보여야 함
        )

        self.client.force_authenticate(user=self.owner)
        resp = self.client.get("/api/v1/billing/cards/", **self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["card_number_masked"], "**** 1111")
