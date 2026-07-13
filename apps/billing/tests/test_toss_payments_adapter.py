from __future__ import annotations

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.billing.adapters.toss_payments import TossPaymentsClient


class TossPaymentsAdapterTests(SimpleTestCase):
    @patch("apps.billing.adapters.toss_payments._toss_http_call")
    def test_auto_billing_uses_official_idempotency_key_header(self, http_call):
        response = Mock(status_code=200, content=b"{}")
        response.json.return_value = {
            "paymentKey": "pay_1",
            "status": "DONE",
        }
        http_call.return_value = response
        client = TossPaymentsClient(secret_key="test_secret")

        result = client.charge_with_billing_key(
            billing_key="billing-key",
            customer_key="customer-key",
            amount=110_000,
            order_id="ord-idempotent-1",
            order_name="월 구독",
        )

        self.assertTrue(result["success"])
        headers = http_call.call_args.args[2]
        self.assertEqual(headers["Idempotency-Key"], "ord-idempotent-1")

    @patch("apps.billing.adapters.toss_payments._toss_http_call")
    def test_payment_query_uses_order_endpoint(self, http_call):
        response = Mock(status_code=200, content=b"{}")
        response.json.return_value = {
            "type": "BILLING",
            "orderId": "ord/query",
            "status": "DONE",
        }
        http_call.return_value = response
        client = TossPaymentsClient(secret_key="test_secret")

        result = client.get_payment_by_order_id("ord/query")

        self.assertTrue(result["success"])
        self.assertTrue(http_call.call_args.args[1].endswith("/payments/orders/ord%2Fquery"))

    @patch("apps.billing.adapters.toss_payments._toss_http_call")
    def test_duplicate_order_response_is_not_classified_as_definite_rejection(
        self,
        http_call,
    ):
        response = Mock(status_code=400, content=b"{}")
        response.json.return_value = {
            "code": "DUPLICATED_ORDER_ID",
            "message": "already approved or canceled",
        }
        http_call.return_value = response
        client = TossPaymentsClient(secret_key="test_secret")

        result = client.charge_with_billing_key(
            billing_key="billing-key",
            customer_key="customer-key",
            amount=110_000,
            order_id="ord-ambiguous-1",
            order_name="월 구독",
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["outcome_unknown"])
        self.assertFalse(result["definitely_rejected"])
