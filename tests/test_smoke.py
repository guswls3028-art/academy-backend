"""
CI Smoke Tests - Deploy Gate
=============================
Minimal smoke tests that verify the Django backend boots correctly
and critical endpoints respond as expected.

Requirements:
- SQLite in-memory DB (no PostgreSQL)
- No external services (no AWS, Redis, SQS, R2)
- Deterministic, < 30 seconds total

Run:  pytest tests/test_smoke.py
"""

from django.test import TestCase, RequestFactory
from django.conf import settings


class TestHealthEndpoints(TestCase):
    """Health check endpoints must respond correctly."""

    def test_healthz_returns_200(self):
        """GET /healthz -> 200 (liveness, no DB dependency)."""
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")

    def test_health_returns_200(self):
        """GET /health -> 200 (readiness, checks DB via SQLite in test)."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["database"], "connected")

    def test_readyz_returns_200(self):
        """GET /readyz -> 200 (readiness, checks DB via SQLite in test)."""
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["database"], "connected")



class TestAuthRequired(TestCase):
    """API endpoints must require authentication."""

    def test_api_requires_auth(self):
        """GET /api/v1/core/me/ without auth -> 401 (or tenant error first).

        Since tenant middleware runs before auth, an unknown host may return
        a tenant error. We test that the endpoint does NOT return 200.
        """
        response = self.client.get("/api/v1/core/me/")
        self.assertNotEqual(
            response.status_code,
            200,
            "/api/v1/core/me/ should not return 200 without authentication",
        )

    def test_token_endpoint_exists(self):
        """POST /api/v1/token/ is a registered URL (not 404).

        Uses Django's URL resolver directly since tenant middleware
        intercepts requests before they reach the view for unknown hosts.
        """
        from django.urls import resolve, Resolver404

        try:
            match = resolve("/api/v1/token/")
        except Resolver404:
            self.fail("/api/v1/token/ does not resolve (404)")

        self.assertIsNotNone(match)
        self.assertEqual(match.url_name, "token_obtain_pair")



class TestModelsImportable(TestCase):
    """Critical models must be importable without errors."""

    def test_models_importable(self):
        """Import key models: Tenant, User, Session, Student."""
        from apps.core.models.tenant import Tenant
        from apps.core.models.user import User
        from apps.domains.lectures.models import Session
        from apps.domains.students.models import Student

        # Verify they are actual Django models
        for model in [Tenant, User, Session, Student]:
            self.assertTrue(
                hasattr(model, "_meta"),
                f"{model.__name__} is not a Django model (no _meta)",
            )
            self.assertTrue(
                hasattr(model, "objects"),
                f"{model.__name__} has no default manager",
            )


class TestTenantIsolation(TestCase):
    """Tenant middleware must reject requests without valid tenant context."""

    def test_unknown_host_blocked(self):
        """Request with unknown Host to tenant-required path -> not 200."""
        response = self.client.get(
            "/api/v1/core/me/",
            HTTP_HOST="evil-academy.example.com",
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertIn(response.status_code, [400, 403, 404])

    def test_tenant_endpoints_resolve(self):
        """Critical tenant-scoped endpoints must be registered (URL resolver)."""
        from django.urls import resolve, Resolver404

        tenant_paths = [
            "/api/v1/clinic/sessions/",
            "/api/v1/students/",
            "/api/v1/community/posts/",
        ]
        for path in tenant_paths:
            try:
                match = resolve(path)
                self.assertIsNotNone(match, f"{path} resolved to None")
            except Resolver404:
                self.fail(f"{path} does not resolve (404)")


class TestCorsPolicy(TestCase):
    """CORS must not allow all origins."""

    def test_cors_not_allow_all(self):
        """CORS_ALLOW_ALL_ORIGINS must be False."""
        self.assertFalse(
            settings.CORS_ALLOW_ALL_ORIGINS,
            "CORS_ALLOW_ALL_ORIGINS must be False in production-like settings",
        )

    def test_cors_allowed_origins_not_empty(self):
        """CORS_ALLOWED_ORIGINS must have at least one entry."""
        origins = getattr(settings, "CORS_ALLOWED_ORIGINS", [])
        self.assertGreater(len(origins), 0, "No CORS allowed origins configured")


class TestAuthFailure(TestCase):
    """Authentication failures must return proper error responses."""

    def test_invalid_jwt_rejected(self):
        """Request with invalid Bearer token -> 401."""
        response = self.client.get(
            "/api/v1/core/me/",
            HTTP_AUTHORIZATION="Bearer invalid.jwt.token",
        )
        # Tenant middleware may intercept first, but must not return 200
        self.assertNotEqual(response.status_code, 200)


class TestWriteApiRegistered(TestCase):
    """Critical write endpoints must be registered."""

    def test_clinic_participant_create_registered(self):
        """POST /api/v1/clinic/participants/ must be a valid route."""
        from django.urls import resolve, Resolver404

        try:
            match = resolve("/api/v1/clinic/participants/")
            self.assertIsNotNone(match)
        except Resolver404:
            self.fail("/api/v1/clinic/participants/ does not resolve")

    def test_session_create_registered(self):
        """POST /api/v1/clinic/sessions/ must be a valid route."""
        from django.urls import resolve, Resolver404

        try:
            match = resolve("/api/v1/clinic/sessions/")
            self.assertIsNotNone(match)
        except Resolver404:
            self.fail("/api/v1/clinic/sessions/ does not resolve")


class TestSettingsIntegrity(TestCase):
    """Test settings must be safe for CI."""

    def test_settings_no_debug_in_test(self):
        """DEBUG must be False in test mode."""
        self.assertFalse(
            settings.DEBUG,
            "DEBUG must be False in test settings",
        )

    def test_correlation_middleware_first(self):
        """CorrelationIdMiddleware must be the first middleware."""
        middleware = settings.MIDDLEWARE
        correlation_mw = "apps.api.common.correlation.CorrelationIdMiddleware"
        self.assertIn(correlation_mw, middleware, "Correlation middleware missing")
        self.assertEqual(
            middleware.index(correlation_mw), 0,
            "Correlation middleware must be first in MIDDLEWARE list",
        )
