# PATH: apps/core/tests/test_jwt_tenant_claim.py
"""
보안 회귀 — JWT tenant_id claim 교차 검증 (H-4, 2026-04-25).

TokenVersionJWTAuthentication.get_user 가 validated_token 의 tenant_id 와
현재 tenant 컨텍스트를 비교해 mismatch 시 즉시 차단하는지.

두 claim은 필수다. 누락/구형 refresh에서 파생된 access token은 재로그인을
요구하고, tenant 컨텍스트가 없으면 claim tenant와 멤버십을 fail-closed 검증한다.
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

    def test_missing_claims_require_relogin(self):
        with self._patch_parent():
            for token in (
                {"tenant_id": 7},
                {"token_version": 0},
                {},
            ):
                with self.subTest(token=token):
                    with self.assertRaises(AuthenticationFailed) as ctx:
                        self.auth.get_user(token)
                    self.assertIn("다시 로그인", str(ctx.exception.detail))

    def test_matching_claim_passes(self):
        """claim == 현재 tenant → 통과."""
        set_current_tenant(_FakeTenant(7))
        with self._patch_parent(), patch(
            "apps.core.services.tenant_access.user_has_active_tenant_access",
            return_value=True,
        ):
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

    def test_no_context_missing_claim_tenant_fails_closed(self):
        """미들웨어 컨텍스트가 없어도 존재하지 않는 claim tenant는 차단한다."""
        clear_current_tenant()
        with self._patch_parent():
            with self.assertRaises(AuthenticationFailed):
                self.auth.get_user({"token_version": 0, "tenant_id": 7})

    def test_no_context_resolves_claim_tenant_and_membership(self):
        tenant = _FakeTenant(7)
        clear_current_tenant()
        with (
            self._patch_parent(),
            patch(
                "academy.adapters.db.django.repositories_core.tenant_get_by_id",
                return_value=tenant,
            ),
            patch(
                "apps.core.services.tenant_access.user_has_active_tenant_access",
                return_value=True,
            ) as has_access,
        ):
            user = self.auth.get_user({"token_version": 0, "tenant_id": 7})
        self.assertTrue(user.is_authenticated)
        has_access.assert_called_once_with(user, tenant)
