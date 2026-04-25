# PATH: apps/core/tests/test_jwt_tenant_claim.py
"""
보안 회귀 — JWT tenant_id claim 교차 검증 (H-4, 2026-04-25).

TokenVersionJWTAuthentication.get_user 가 validated_token 의 tenant_id 와
현재 tenant 컨텍스트를 비교해 mismatch 시 즉시 차단하는지.

마이그레이션 안전성 케이스도 같이 본다:
  - claim 없는 토큰 → skip
  - tenant 컨텍스트 미설정 → fail-open (권한 단계로 위임)
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from rest_framework_simplejwt.exceptions import AuthenticationFailed

from apps.core.authentication import TokenVersionJWTAuthentication
from apps.core.tenant.context import set_current_tenant, clear_current_tenant


class _FakeTenant:
    def __init__(self, tid):
        self.id = tid


class _FakeUser:
    is_authenticated = True
    token_version = 0
    tenant_id = 7


class TestJwtTenantClaim(TestCase):

    def setUp(self):
        self.auth = TokenVersionJWTAuthentication()

    def tearDown(self):
        clear_current_tenant()

    def _patch_parent(self):
        # 부모 JWTAuthentication.get_user 는 DB lookup을 하므로 격리.
        return patch.object(
            TokenVersionJWTAuthentication.__bases__[0],
            "get_user",
            return_value=_FakeUser(),
        )

    def test_no_claim_passes(self):
        """tenant_id claim 없는 토큰(마이그레이션 전 발급분) → 통과."""
        with self._patch_parent():
            user = self.auth.get_user({"token_version": 0})
        self.assertTrue(user.is_authenticated)

    def test_matching_claim_passes(self):
        """claim == 현재 tenant → 통과."""
        set_current_tenant(_FakeTenant(7))
        with self._patch_parent():
            user = self.auth.get_user({"token_version": 0, "tenant_id": 7})
        self.assertTrue(user.is_authenticated)

    def test_mismatched_claim_blocked(self):
        """claim != 현재 tenant → AuthenticationFailed."""
        set_current_tenant(_FakeTenant(7))
        with self._patch_parent():
            with self.assertRaises(AuthenticationFailed) as ctx:
                self.auth.get_user({"token_version": 0, "tenant_id": 99})
        # 메시지에 식별 가능한 단서 포함
        self.assertIn("학원", str(ctx.exception.detail))

    def test_no_context_fail_open(self):
        """tenant 컨텍스트 미설정 + claim 있음 → fail-open (권한 단계 위임)."""
        clear_current_tenant()
        with self._patch_parent():
            user = self.auth.get_user({"token_version": 0, "tenant_id": 7})
        self.assertTrue(user.is_authenticated)
