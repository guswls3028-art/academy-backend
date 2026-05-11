"""
MustChangePasswordGate 회귀 테스트.

검증 항목:
- mcp claim 없는 토큰: 모든 경로 통과 (일반 사용자 영향 없음)
- mcp=False 토큰: 모든 경로 통과
- mcp=True 토큰:
  - 비번 변경 API/로그아웃/refresh/me 정확 일치 → 통과
  - 그 외 임의 endpoint → 403
- Authorization 헤더 없음: 통과 (인증 안 된 요청은 게이트 무관)
- BYPASS_PREFIXES (admin/internal/health): 통과
"""
from __future__ import annotations

import json

from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from apps.api.common.middleware import MustChangePasswordGate


def _bearer(claims: dict) -> str:
    """tests/는 simplejwt 의 AccessToken 을 직접 사용해 토큰 생성."""
    from rest_framework_simplejwt.tokens import AccessToken
    tok = AccessToken()
    for k, v in claims.items():
        tok[k] = v
    return f"Bearer {str(tok)}"


class TestMustChangePasswordGate(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.gate = MustChangePasswordGate(get_response=lambda req: HttpResponse(b"OK"))

    def _request(self, path: str, *, auth: str | None = None):
        req = self.factory.get(path)
        if auth:
            req.META["HTTP_AUTHORIZATION"] = auth
        return req

    def test_no_auth_header_passes(self):
        resp = self.gate(self._request("/api/v1/students/"))
        self.assertEqual(resp.status_code, 200)

    def test_bypass_path_passes(self):
        resp = self.gate(self._request("/admin/students/"))
        self.assertEqual(resp.status_code, 200)
        resp = self.gate(self._request("/api/v1/internal/anything/"))
        self.assertEqual(resp.status_code, 200)

    def test_token_without_mcp_claim_passes(self):
        # mcp claim 없는 토큰 (기존 사용자 토큰) — 통과
        resp = self.gate(self._request("/api/v1/students/", auth=_bearer({})))
        self.assertEqual(resp.status_code, 200)

    def test_mcp_false_passes(self):
        resp = self.gate(self._request("/api/v1/students/", auth=_bearer({"mcp": False})))
        self.assertEqual(resp.status_code, 200)

    def test_mcp_true_blocks_arbitrary_endpoint(self):
        resp = self.gate(self._request("/api/v1/students/", auth=_bearer({"mcp": True})))
        self.assertEqual(resp.status_code, 403)
        body = json.loads(resp.content)
        self.assertEqual(body.get("code"), "must_change_password")

    def test_mcp_true_allows_change_password(self):
        resp = self.gate(self._request("/api/v1/auth/change-password/", auth=_bearer({"mcp": True})))
        self.assertEqual(resp.status_code, 200)
        resp = self.gate(self._request(
            "/api/v1/core/me/profile/change-password/", auth=_bearer({"mcp": True}),
        ))
        self.assertEqual(resp.status_code, 200)

    def test_mcp_true_allows_logout_and_refresh(self):
        resp = self.gate(self._request("/api/v1/auth/logout/", auth=_bearer({"mcp": True})))
        self.assertEqual(resp.status_code, 200)
        resp = self.gate(self._request("/api/v1/token/refresh/", auth=_bearer({"mcp": True})))
        # /api/v1/token/ 은 BYPASS_PREFIXES 라 무조건 통과
        self.assertEqual(resp.status_code, 200)

    def test_mcp_true_me_exact_passes_but_subpath_blocked(self):
        # /api/v1/core/me/ 정확 일치 → 통과 (플래그 확인용)
        resp = self.gate(self._request("/api/v1/core/me/", auth=_bearer({"mcp": True})))
        self.assertEqual(resp.status_code, 200)
        # /api/v1/core/me/profile/ 등 sub-path → 차단 (데이터 조회 막아야 함)
        resp = self.gate(self._request(
            "/api/v1/core/me/profile/photo/", auth=_bearer({"mcp": True}),
        ))
        self.assertEqual(resp.status_code, 403)

    def test_invalid_token_passes_to_drf_for_401(self):
        # 깨진 토큰 → 게이트 통과 (이후 DRF 가 401 처리)
        resp = self.gate(self._request("/api/v1/students/", auth="Bearer not-a-real-jwt"))
        self.assertEqual(resp.status_code, 200)
