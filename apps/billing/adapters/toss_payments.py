"""
Toss Payments API 클라이언트

공식 문서 기반:
- 빌링키 발급: POST /v1/billing/authorizations/issue
- 빌링키 삭제: DELETE /v1/billing/{billingKey}  (POST 아님)
- 인증: Basic auth (secret key : 빈 비밀번호)
- 타임아웃: 결제 API는 최소 60초
"""

from __future__ import annotations

import base64
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
