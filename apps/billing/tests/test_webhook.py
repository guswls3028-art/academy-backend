"""
Toss 웹훅 테스트.

범위:
- 서명 검증 (HMAC-SHA256)
- PAYMENT_STATUS_CHANGED 이벤트 처리 (DONE/ABORTED/CANCELED)
- 멱등성 (동일 이벤트 재수신 시 중복 적용 방지)
- 종단 상태 덮어쓰기 방지
"""

import base64
import hashlib
import hmac
import json
from datetime import date, timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.billing.adapters.toss_payments import verify_webhook_signature
from apps.billing.models import BillingKey, BillingProfile, Invoice, PaymentTransaction
from apps.billing.services import webhook_service
from apps.core.models import Tenant
from apps.core.models.program import Program


WEBHOOK_SECRET = "whsec_test_dummy_01234"


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


class TestSignatureVerification(TestCase):

    def test_valid_signature_accepted(self):
        body = b'{"eventType":"PAYMENT_STATUS_CHANGED"}'
        sig = _sign(body)
        self.assertTrue(verify_webhook_signature(body, sig, WEBHOOK_SECRET))

    def test_invalid_signature_rejected(self):
        body = b'{"eventType":"PAYMENT_STATUS_CHANGED"}'
        self.assertFalse(verify_webhook_signature(body, "invalid_sig", WEBHOOK_SECRET))

    def test_empty_secret_rejected(self):
        body = b'{}'
        sig = _sign(body)
        self.assertFalse(verify_webhook_signature(body, sig, ""))

    def test_empty_signature_rejected(self):
        body = b'{}'
        self.assertFalse(verify_webhook_signature(body, "", WEBHOOK_SECRET))

    def test_v1_prefix_stripped(self):
        body = b'{"test":1}'
        sig = "v1=" + _sign(body)
        self.assertTrue(verify_webhook_signature(body, sig, WEBHOOK_SECRET))


@override_settings(
    TOSS_WEBHOOK_SECRET=WEBHOOK_SECRET,
    TOSS_AUTO_BILLING_ENABLED=True,
    BILLING_EXEMPT_TENANT_IDS=set(),
)
class TestWebhookEndpointBase(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="웹훅학원", code="webhook_test", is_active=True)
        self.program = Program.objects.get(tenant=self.tenant)
        self.program.subscription_status = "active"
        self.program.subscription_expires_at = date.today() + timedelta(days=5)
        self.program.plan = "pro"
        self.program.monthly_price = 198_000
        self.program.billing_mode = "AUTO_CARD"
        self.program.save()

        self.profile = BillingProfile.objects.create(tenant=self.tenant)
        self.billing_key = BillingKey.objects.create(
            tenant=self.tenant,
            billing_profile=self.profile,
            billing_key="bk_wh_test",
            is_active=True,
        )
        self.invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-WH-001",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=date.today(),
            period_end=date.today() + timedelta(days=30),
            due_date=date.today(),
            status="PENDING",
        )
        self.tx = PaymentTransaction.objects.create(
            tenant=self.tenant,
            invoice=self.invoice,
            provider="tosspayments",
            provider_order_id=self.invoice.provider_order_id,
            idempotency_key=self.invoice.provider_order_id,
            amount=self.invoice.total_amount,
            status="PENDING",
        )
        self.url = "/api/v1/billing/webhooks/toss/"
        self.client = APIClient()


class TestWebhookDone(TestWebhookEndpointBase):

    def _payload(self, status="DONE"):
        return {
            "eventType": "PAYMENT_STATUS_CHANGED",
            "data": {
                "orderId": self.invoice.provider_order_id,
                "paymentKey": "pay_wh_abc",
                "status": status,
                "approvedAt": "2026-04-20T12:34:56+09:00",
                "card": {"company": "국민", "number": "**** **** **** 4321"},
                "totalAmount": 217800,
            },
        }

    def test_done_marks_paid(self):
        body = json.dumps(self._payload()).encode()
        sig = _sign(body)
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_TOSSPAYMENTS_SIGNATURE=sig,
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.invoice.refresh_from_db()
        self.tx.refresh_from_db()
        self.assertEqual(self.invoice.status, "PAID")
        self.assertEqual(self.tx.status, "SUCCESS")
        self.assertEqual(self.tx.provider_payment_key, "pay_wh_abc")

    def test_invalid_signature_returns_401(self):
        body = json.dumps(self._payload()).encode()
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_TOSSPAYMENTS_SIGNATURE="wrong_sig",
        )
        self.assertEqual(resp.status_code, 401)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PENDING")  # 변경 안 됨

    def test_aborted_marks_failed(self):
        payload = self._payload(status="ABORTED")
        body = json.dumps(payload).encode()
        sig = _sign(body)
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_TOSSPAYMENTS_SIGNATURE=sig,
        )
        self.assertEqual(resp.status_code, 200)
        self.invoice.refresh_from_db()
        self.tx.refresh_from_db()
        self.assertEqual(self.invoice.status, "FAILED")
        self.assertEqual(self.tx.status, "FAILED")

    def test_idempotent_duplicate_done_ignored(self):
        body = json.dumps(self._payload()).encode()
        sig = _sign(body)
        r1 = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_TOSSPAYMENTS_SIGNATURE=sig,
        )
        r2 = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_TOSSPAYMENTS_SIGNATURE=sig,
        )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PAID")

    def test_success_tx_not_overwritten_by_aborted(self):
        """이미 SUCCESS인 tx를 ABORTED 이벤트가 덮어쓰지 않아야 함 (순서 꼬임 방어)"""
        self.tx.status = "SUCCESS"
        self.tx.save()
        self.invoice.status = "PAID"
        self.invoice.paid_at = self.invoice.created_at
        self.invoice.save()

        payload = self._payload(status="ABORTED")
        body = json.dumps(payload).encode()
        sig = _sign(body)
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_TOSSPAYMENTS_SIGNATURE=sig,
        )
        self.assertEqual(resp.status_code, 200)
        self.tx.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.tx.status, "SUCCESS")
        self.assertEqual(self.invoice.status, "PAID")


@override_settings(TOSS_WEBHOOK_SECRET=WEBHOOK_SECRET)
class TestWebhookUnmatched(TestWebhookEndpointBase):

    def test_unknown_order_id_recovers_via_invoice(self):
        """orderId로 tx는 없지만 invoice는 있는 케이스 — webhook이 tx를 생성"""
        # tx 제거
        self.tx.delete()

        payload = {
            "eventType": "PAYMENT_STATUS_CHANGED",
            "data": {
                "orderId": self.invoice.provider_order_id,
                "paymentKey": "pay_recov",
                "status": "DONE",
            },
        }
        body = json.dumps(payload).encode()
        sig = _sign(body)
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_TOSSPAYMENTS_SIGNATURE=sig,
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PAID")

    def test_totally_unknown_order_id_returns_200_unmatched(self):
        payload = {
            "eventType": "PAYMENT_STATUS_CHANGED",
            "data": {"orderId": "ord_unknown_xxx", "status": "DONE", "paymentKey": "pay_x"},
        }
        body = json.dumps(payload).encode()
        sig = _sign(body)
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_TOSSPAYMENTS_SIGNATURE=sig,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("result"), "unmatched")


@override_settings(TOSS_WEBHOOK_SECRET=WEBHOOK_SECRET)
class TestWebhookUnhandledEvent(TestWebhookEndpointBase):

    def test_unknown_event_type_returns_200(self):
        payload = {"eventType": "SOMETHING_ELSE", "data": {}}
        body = json.dumps(payload).encode()
        sig = _sign(body)
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_TOSSPAYMENTS_SIGNATURE=sig,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("result"), "unhandled_event_type")
