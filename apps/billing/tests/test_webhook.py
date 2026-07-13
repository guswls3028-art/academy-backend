"""
Toss 웹훅 테스트.

범위:
- 서버 간 Payment Query 검증
- PAYMENT_STATUS_CHANGED 이벤트 처리 (DONE/ABORTED/CANCELED)
- 멱등성 (동일 이벤트 재수신 시 중복 적용 방지)
- 종단 상태 덮어쓰기 방지
"""

import json
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.billing.models import BillingKey, BillingProfile, Invoice, PaymentTransaction
from apps.core.models import OpsAuditLog, Tenant
from apps.core.models.program import Program


@override_settings(
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
        self.query_patcher = patch(
            "apps.billing.adapters.toss_payments.TossPaymentsClient.get_payment_by_order_id"
        )
        self.query_payment = self.query_patcher.start()
        self.addCleanup(self.query_patcher.stop)


class TestWebhookDone(TestWebhookEndpointBase):

    def _payload(self, status="DONE"):
        payload = {
            "eventType": "PAYMENT_STATUS_CHANGED",
            "data": {
                "type": "BILLING",
                "orderId": self.invoice.provider_order_id,
                "paymentKey": "pay_wh_abc",
                "status": status,
                "approvedAt": "2026-04-20T12:34:56+09:00",
                "card": {"company": "국민", "number": "**** **** **** 4321"},
                "totalAmount": 217800,
            },
        }
        self.query_payment.return_value = {"success": True, **payload["data"]}
        return payload

    def test_done_marks_paid(self):
        body = json.dumps(self._payload()).encode()
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.invoice.refresh_from_db()
        self.tx.refresh_from_db()
        self.assertEqual(self.invoice.status, "PAID")
        self.assertEqual(self.tx.status, "SUCCESS")
        self.assertEqual(self.tx.provider_payment_key, "pay_wh_abc")

    def test_flat_payload_without_official_event_type_is_ignored(self):
        body = json.dumps(self._payload()["data"]).encode()
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json().get("result"), "unhandled_event_type")
        self.invoice.refresh_from_db()
        self.tx.refresh_from_db()
        self.assertEqual(self.invoice.status, "PENDING")
        self.assertEqual(self.tx.status, "PENDING")

    def test_signature_header_is_not_required_for_payment_event(self):
        body = json.dumps(self._payload()).encode()
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PAID")

    def test_query_failure_fails_closed_without_local_mutation(self):
        body = json.dumps(self._payload()).encode()
        self.query_payment.return_value = {
            "success": False,
            "error_code": "UPSTREAM_5XX",
        }

        response = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503, response.content)
        self.invoice.refresh_from_db()
        self.tx.refresh_from_db()
        self.assertEqual(self.invoice.status, "PENDING")
        self.assertEqual(self.tx.status, "PENDING")

    def test_verified_amount_mismatch_is_rejected(self):
        body = json.dumps(self._payload()).encode()
        self.query_payment.return_value["totalAmount"] = self.invoice.total_amount + 1

        response = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"], "provider_mismatch")
        self.invoice.refresh_from_db()
        self.tx.refresh_from_db()
        self.assertEqual(self.invoice.status, "PENDING")
        self.assertEqual(self.tx.status, "PENDING")

    def test_verified_payment_key_conflict_is_rejected(self):
        self.tx.provider_payment_key = "pay_existing"
        self.tx.save(update_fields=["provider_payment_key"])
        body = json.dumps(self._payload()).encode()

        response = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"], "provider_mismatch")
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, "PENDING")

    def test_done_for_void_invoice_preserves_capture_for_manual_reconciliation(self):
        self.invoice.status = "VOID"
        self.invoice.save(update_fields=["status"])
        body = json.dumps(self._payload()).encode()

        response = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json()["result"],
            "payment_captured_invoice_terminal",
        )
        self.tx.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.tx.status, "SUCCESS")
        self.assertEqual(self.invoice.status, "VOID")

    def test_aborted_marks_failed(self):
        payload = self._payload(status="ABORTED")
        body = json.dumps(payload).encode()
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.invoice.refresh_from_db()
        self.tx.refresh_from_db()
        self.assertEqual(self.invoice.status, "FAILED")
        self.assertEqual(self.tx.status, "FAILED")

    def test_idempotent_duplicate_done_ignored(self):
        body = json.dumps(self._payload()).encode()
        r1 = self.client.post(
            self.url, data=body, content_type="application/json",
        )
        r2 = self.client.post(
            self.url, data=body, content_type="application/json",
        )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PAID")

    def test_existing_success_transaction_repairs_pending_invoice(self):
        self.tx.status = "SUCCESS"
        self.tx.provider_payment_key = "pay_wh_abc"
        self.tx.processed_at = self.invoice.created_at
        self.tx.save(
            update_fields=["status", "provider_payment_key", "processed_at"]
        )
        body = json.dumps(self._payload()).encode()

        response = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"], "repaired_success_invoice")
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
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.tx.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.tx.status, "SUCCESS")
        self.assertEqual(self.invoice.status, "PAID")

    def test_canceled_after_success_voids_invoice_and_reconciles_subscription(self):
        done_body = json.dumps(self._payload()).encode()
        done_resp = self.client.post(
            self.url, data=done_body, content_type="application/json",
        )
        self.assertEqual(done_resp.status_code, 200, done_resp.content)

        cancel_payload = self._payload(status="CANCELED")
        cancel_payload["data"]["canceledAt"] = "2026-04-21T12:34:56+09:00"
        cancel_body = json.dumps(cancel_payload).encode()
        cancel_resp = self.client.post(
            self.url, data=cancel_body, content_type="application/json",
        )

        self.assertEqual(cancel_resp.status_code, 200, cancel_resp.content)
        self.tx.refresh_from_db()
        self.invoice.refresh_from_db()
        self.program.refresh_from_db()
        self.assertEqual(self.tx.status, "REFUNDED")
        self.assertEqual(self.tx.refunded_amount, self.tx.amount)
        self.assertEqual(self.invoice.status, "VOID")
        self.assertIsNone(self.invoice.paid_at)
        self.assertEqual(self.program.subscription_status, "expired")
        self.assertEqual(self.program.subscription_expires_at, self.invoice.period_start - timedelta(days=1))

    def test_partial_cancel_rejects_non_numeric_balance(self):
        done_body = json.dumps(self._payload()).encode()
        done_response = self.client.post(
            self.url,
            data=done_body,
            content_type="application/json",
        )
        self.assertEqual(done_response.status_code, 200, done_response.content)

        partial = self._payload(status="PARTIAL_CANCELED")
        self.query_payment.return_value["balanceAmount"] = "not-a-number"
        response = self.client.post(
            self.url,
            data=json.dumps(partial).encode(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"], "provider_mismatch")
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, "SUCCESS")

    def test_partial_refund_records_operator_reconciliation_evidence(self):
        done_body = json.dumps(self._payload()).encode()
        done_response = self.client.post(
            self.url,
            data=done_body,
            content_type="application/json",
        )
        self.assertEqual(done_response.status_code, 200, done_response.content)

        partial = self._payload(status="PARTIAL_CANCELED")
        self.query_payment.return_value["balanceAmount"] = (
            self.invoice.total_amount - 10_000
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url,
                data=json.dumps(partial).encode(),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"], "applied_partial_refund")
        self.tx.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.tx.status, "PARTIALLY_REFUNDED")
        self.assertEqual(self.invoice.status, "PAID")
        audit = OpsAuditLog.objects.get(
            action="billing.partial_refund_reconciliation_required"
        )
        self.assertEqual(audit.payload["transaction_id"], self.tx.id)
        self.assertNotIn("payment_key", audit.payload)


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
        self.query_payment.return_value = {
            "success": True,
            "type": "BILLING",
            "orderId": self.invoice.provider_order_id,
            "paymentKey": "pay_recov",
            "status": "DONE",
            "totalAmount": self.invoice.total_amount,
        }
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
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
        self.query_payment.return_value = {
            "success": True,
            "type": "BILLING",
            "orderId": "ord_unknown_xxx",
            "status": "DONE",
            "paymentKey": "pay_x",
            "totalAmount": 1,
        }
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("result"), "unmatched")
        self.query_payment.assert_not_called()

    def test_malformed_order_id_is_rejected_before_provider_query(self):
        payload = {
            "eventType": "PAYMENT_STATUS_CHANGED",
            "data": {"orderId": "../not-a-provider-order", "status": "DONE"},
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload).encode(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.query_payment.assert_not_called()


class TestWebhookUnhandledEvent(TestWebhookEndpointBase):

    def test_non_object_event_is_rejected(self):
        response = self.client.post(
            self.url,
            data=json.dumps(["not", "an", "event"]).encode(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_non_object_data_is_rejected(self):
        response = self.client.post(
            self.url,
            data=json.dumps(
                {"eventType": "PAYMENT_STATUS_CHANGED", "data": ["bad"]}
            ).encode(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_unknown_event_type_returns_200(self):
        payload = {"eventType": "SOMETHING_ELSE", "data": {}}
        body = json.dumps(payload).encode()
        resp = self.client.post(
            self.url, data=body, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("result"), "unhandled_event_type")
