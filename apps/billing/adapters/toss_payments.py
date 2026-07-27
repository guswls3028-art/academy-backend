"""
Toss Payments API 클라이언트

공식 문서 기반:
- 빌링키 발급: POST /v1/billing/authorizations/issue
- 빌링키 삭제: DELETE /v1/billing/{billingKey}
- 빌링키 자동결제: POST /v1/billing/{billingKey}
- 주문 결제 조회: GET /v1/payments/orders/{orderId}
- 인증: Basic auth (secret key : 빈 비밀번호)
- 자동결제 멱등: Idempotency-Key 헤더 (orderId)
- 타임아웃: 결제 API는 최소 60초
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import requests
from django.conf import settings

from apps.shared.utils.circuit_breaker import (
    CircuitOpenError,
    circuit_breaker,
)

logger = logging.getLogger(__name__)

TOSS_API_BASE = "https://api.tosspayments.com/v1"
TOSS_TIMEOUT_SECONDS = 60
TOSS_CARD_ISSUER_NAMES = {
    "3K": "기업 BC",
    "46": "광주은행",
    "71": "롯데카드",
    "30": "한국산업은행",
    "31": "BC카드",
    "51": "삼성카드",
    "38": "새마을금고",
    "41": "신한카드",
    "62": "신협",
    "36": "씨티카드",
    "33": "우리BC카드",
    "W1": "우리카드",
    "37": "우체국",
    "39": "저축은행",
    "35": "전북은행",
    "42": "제주은행",
    "15": "카카오뱅크",
    "3A": "케이뱅크",
    "24": "토스뱅크",
    "21": "하나카드",
    "61": "현대카드",
    "11": "KB국민카드",
    "91": "NH농협카드",
    "34": "Sh수협은행",
}
TOSS_AMBIGUOUS_MUTATION_ERROR_CODES = frozenset(
    {
        "DUPLICATED_ORDER_ID",
        "ALREADY_PROCESSED_PAYMENT",
        "ALREADY_COMPLETED_PAYMENT",
        "DUPLICATED_REQUEST",
        "PROVIDER_ERROR",
    }
)


def resolve_card_company(card: Mapping[str, Any] | None) -> str:
    """Return a user-facing issuer name for legacy and current Toss payloads."""
    if not isinstance(card, Mapping):
        return ""

    legacy_company = card.get("company")
    if isinstance(legacy_company, str) and legacy_company.strip():
        return legacy_company.strip()[:50]

    issuer_code = card.get("issuerCode")
    if not isinstance(issuer_code, str) or not issuer_code.strip():
        return ""
    normalized_code = issuer_code.strip().upper()
    return TOSS_CARD_ISSUER_NAMES.get(normalized_code, normalized_code)[:50]


class _TossUpstreamError(Exception):
    """Toss 서버 측 5xx 또는 네트워크 장애 — circuit breaker가 카운트하는 실패."""


@circuit_breaker(
    name="toss_payments",
    failure_threshold=5,
    window_seconds=30,
    cooldown_seconds=60,
    expected_exceptions=[_TossUpstreamError],
)
def _toss_http_call(method: str, url: str, headers: dict, json_body: dict | None, timeout: int):
    """Toss HTTP 호출 — 5xx/네트워크 장애만 circuit failure로 카운트.
    4xx(business error)는 Toss 에러이므로 circuit과 무관 — 정상 response 반환.
    """
    try:
        resp = requests.request(method, url, headers=headers, json=json_body, timeout=timeout)
    except (requests.Timeout, requests.RequestException) as exc:
        raise _TossUpstreamError(str(exc)) from exc
    if 500 <= resp.status_code < 600:
        raise _TossUpstreamError(f"Toss 5xx: {resp.status_code}")
    return resp


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
        idempotency_key: str = "",
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
            headers = self._auth_header()
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            resp = _toss_http_call(
                method,
                url,
                headers,
                json_body,
                timeout,
            )
        except CircuitOpenError as exc:
            logger.warning("Toss circuit open: %s %s (%.0fs cooldown)", method, path, exc.retry_after)
            return {
                "success": False,
                "error_code": "CIRCUIT_OPEN",
                "error_message": "결제 서비스 일시 장애 — 잠시 후 다시 시도해 주세요.",
                "outcome_unknown": True,
            }
        except _TossUpstreamError as exc:
            # 네트워크/5xx — circuit이 카운트했지만 임계 미달이라 통과한 케이스
            msg = str(exc)
            if "Toss 5xx" in msg:
                logger.error("Toss API 5xx: %s %s - %s", method, path, msg)
                return {
                    "success": False,
                    "error_code": "UPSTREAM_5XX",
                    "error_message": msg,
                    "outcome_unknown": True,
                }
            cause_type = type(exc.__cause__).__name__ if exc.__cause__ else ""
            if "timed out" in msg.lower() or cause_type == "Timeout":
                logger.error("Toss API timeout: %s %s", method, path)
                return {
                    "success": False,
                    "error_code": "TIMEOUT",
                    "error_message": f"Request timed out after {timeout}s",
                    "outcome_unknown": True,
                }
            logger.error("Toss API connection error: %s %s - %s", method, path, msg)
            return {
                "success": False,
                "error_code": "CONNECTION_ERROR",
                "error_message": msg,
                "outcome_unknown": True,
            }

        if resp.status_code < 300:
            data = resp.json() if resp.content else {}
            return {"success": True, **data}

        # Toss business error (4xx) — circuit과 무관, 정상 처리
        try:
            error_data = resp.json()
        except ValueError:
            error_data = {"message": resp.text}

        logger.warning(
            "Toss API error: %s %s -> %s %s",
            method, path, resp.status_code, error_data,
        )
        error_code = error_data.get("code", "")
        outcome_unknown = (
            method.upper() != "GET"
            and error_code in TOSS_AMBIGUOUS_MUTATION_ERROR_CODES
        )
        return {
            "success": False,
            "status_code": resp.status_code,
            "error_code": error_code,
            "error_message": error_data.get("message", str(error_data)),
            "outcome_unknown": outcome_unknown,
            "definitely_rejected": not outcome_unknown,
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
        card.issuerCode, card.number on success. Older API versions can still
        return card.company and are normalized by the service layer.
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
        return self._request("DELETE", f"/billing/{quote(str(billing_key), safe='')}")

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

        return self._request(
            "POST",
            f"/billing/{quote(str(billing_key), safe='')}",
            json_body=body,
            idempotency_key=order_id,
        )

    def get_payment_by_order_id(self, order_id: str) -> dict[str, Any]:
        """Fetch authoritative payment state for webhook/reconciliation."""
        return self._request(
            "GET",
            f"/payments/orders/{quote(str(order_id), safe='')}",
        )
