"""
Toss Payments API 클라이언트

공식 문서 기반:
- 빌링키 발급: POST /v1/billing/authorizations/issue
- 빌링키 삭제: DELETE /v1/billing/{billingKey}
- 빌링키 자동결제: POST /v1/billing/{billingKey}
- 인증: Basic auth (secret key : 빈 비밀번호)
- 웹훅 서명: HMAC-SHA256(rawBody, webhook_secret) in base64
- 타임아웃: 결제 API는 최소 60초
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TOSS_API_BASE = "https://api.tosspayments.com/v1"
TOSS_TIMEOUT_SECONDS = 60


class TossPaymentsClient:
    """Toss Payments REST API client for billing key operations."""

    def __init__(self, secret_key: str | None = None):
        self._secret_key = secret_key or settings.TOSS_PAYMENTS_SECRET_KEY

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_header(self) -> dict[str, str]:
        """Basic auth: secret_key as username, empty password."""
        token = base64.b64encode(
            f"{self._secret_key}:".encode()
        ).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        timeout: int = TOSS_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """
        Send request to Toss API.

        Returns dict with:
          - success=True + response data on 2xx
          - success=False + error info on failure
        Never raises on PG errors.
        """
        url = f"{TOSS_API_BASE}{path}"
        try:
            resp = requests.request(
                method,
                url,
                headers=self._auth_header(),
                json=json_body,
                timeout=timeout,
            )

            if resp.status_code < 300:
                data = resp.json() if resp.content else {}
                return {"success": True, **data}

            # Toss error response
            try:
                error_data = resp.json()
            except ValueError:
                error_data = {"message": resp.text}

            logger.warning(
                "Toss API error: %s %s -> %s %s",
                method, path, resp.status_code, error_data,
            )
            return {
                "success": False,
                "status_code": resp.status_code,
                "error_code": error_data.get("code", ""),
                "error_message": error_data.get("message", str(error_data)),
            }

        except requests.Timeout:
            logger.error("Toss API timeout: %s %s", method, path)
            return {
                "success": False,
                "error_code": "TIMEOUT",
                "error_message": f"Request timed out after {timeout}s",
            }
        except requests.RequestException as exc:
            logger.error("Toss API connection error: %s %s - %s", method, path, exc)
            return {
                "success": False,
                "error_code": "CONNECTION_ERROR",
                "error_message": str(exc),
            }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def issue_billing_key(self, auth_key: str, customer_key: str) -> dict[str, Any]:
        """
        빌링키 발급.

        POST /v1/billing/authorizations/issue
        Body: { authKey, customerKey }

        Returns dict with success flag + billingKey, customerKey,
        card.company, card.number on success.
        """
        return self._request(
            "POST",
            "/billing/authorizations/issue",
            json_body={
                "authKey": auth_key,
                "customerKey": customer_key,
            },
        )

    def delete_billing_key(self, billing_key: str) -> dict[str, Any]:
        """
        빌링키 삭제.

        DELETE /v1/billing/{billingKey}
        """
        return self._request("DELETE", f"/billing/{billing_key}")

    def get_billing_key(self, billing_key: str) -> dict[str, Any]:
        """
        빌링키 조회 (검증용).

        GET /v1/billing/{billingKey}
        """
        return self._request("GET", f"/billing/{billing_key}")

    def charge_with_billing_key(
        self,
        *,
        billing_key: str,
        customer_key: str,
        amount: int,
        order_id: str,
        order_name: str,
        customer_email: str = "",
        customer_name: str = "",
        tax_free_amount: int = 0,
    ) -> dict[str, Any]:
        """
        빌링키 자동결제.

        POST /v1/billing/{billingKey}
        Body: { customerKey, amount, orderId, orderName, ... }

        Toss 응답 성공 시 paymentKey, status=DONE, method=카드, card.*, approvedAt 등 포함.
        orderId는 invoice.provider_order_id 사용 (멱등성 보장).
        """
        body: dict[str, Any] = {
            "customerKey": customer_key,
            "amount": amount,
            "orderId": order_id,
            "orderName": order_name,
        }
        if customer_email:
            body["customerEmail"] = customer_email
        if customer_name:
            body["customerName"] = customer_name
        if tax_free_amount:
            body["taxFreeAmount"] = tax_free_amount

        return self._request("POST", f"/billing/{billing_key}", json_body=body)


def verify_webhook_signature(
    raw_body: bytes,
    signature_header: str,
    webhook_secret: str | None = None,
) -> bool:
    """
    Toss 웹훅 서명 검증.

    Toss는 HMAC-SHA256(rawBody, webhook_secret) 결과를 base64로 인코딩해서
    TossPayments-Signature 헤더에 담는다. "v1=..." 형식일 수 있어 prefix 제거.

    webhook_secret 미설정 시 False (개발 중 실수로 인증 우회 방지).
    """
    secret = webhook_secret if webhook_secret is not None else settings.TOSS_WEBHOOK_SECRET
    if not secret or not signature_header:
        return False

    # "v1=..." prefix 제거 (Toss 포맷 유연 대응)
    sig = signature_header.strip()
    if sig.startswith("v1="):
        sig = sig[3:]

    expected = base64.b64encode(
        hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    ).decode()

    return hmac.compare_digest(expected, sig)
