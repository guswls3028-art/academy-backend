import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory

from apps.api.common.auth_jwt import TenantAwareTokenObtainPairSerializer
from apps.core.models import Tenant, TenantDomain, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.middleware.tenant import TenantMiddleware


class TenantMiddlewareBypassTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Header Tenant", code="header-tenant")
        inactive_tenant = Tenant.objects.create(name="Inactive API Tenant", code="inactive-api", is_active=False)
        TenantDomain.objects.create(
            tenant=inactive_tenant,
            host="api.hakwonplus.com",
            is_primary=False,
            is_active=True,
        )

        user_model = get_user_model()
        user = user_model.objects.create_user(
            username=user_internal_username(self.tenant, "admin"),
            password="pw123456",
            tenant=self.tenant,
            must_change_password=False,
            token_version=0,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role="admin")

    @override_settings(
        ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
        TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
        TENANT_BYPASS_PATH_PREFIXES=("/api/v1/token/", "/api/v1/token/refresh/"),
    )
    def test_token_endpoint_bypasses_inactive_api_domain_and_uses_body_tenant_code(self):
        response = APIClient().post(
            "/api/v1/token/",
            {"username": "admin", "password": "pw123456", "tenant_code": self.tenant.code},
            format="json",
            HTTP_HOST="api.hakwonplus.com",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    @override_settings(
        ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
        TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
    )
    def test_token_serializer_uses_initial_data_tenant_code_when_request_data_is_empty(self):
        class RequestStub:
            META = {}
            data = {}

            @staticmethod
            def get_host():
                return "api.hakwonplus.com"

        serializer = TenantAwareTokenObtainPairSerializer(
            data={"username": "admin", "password": "pw123456", "tenant_code": self.tenant.code},
            context={"request": RequestStub()},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIn("access", serializer.validated_data)

    @override_settings(
        ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
        TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
        TENANT_BYPASS_PATH_PREFIXES=("/api/v1/token/", "/api/v1/token/refresh/"),
    )
    def test_non_bypass_path_still_rejects_inactive_api_domain_without_tenant_header(self):
        response = APIClient().get("/api/v1/core/program/", HTTP_HOST="api.hakwonplus.com")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "tenant_inactive")

    @patch(
        "apps.core.middleware.tenant.resolve_tenant_from_request",
        side_effect=RuntimeError("SECRET_DATABASE_DSN_MARKER"),
    )
    def test_unexpected_resolver_error_never_leaks_exception_text(self, _resolve):
        request = APIRequestFactory().get("/api/v1/core/program/")
        request.META["HTTP_HOST"] = "testserver"
        middleware = TenantMiddleware(lambda _request: JsonResponse({"ok": True}))

        response = middleware(request)

        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["code"], "server_error")
        self.assertNotIn("SECRET_DATABASE_DSN_MARKER", response.content.decode())
