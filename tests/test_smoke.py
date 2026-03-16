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


class TestSettingsIntegrity(TestCase):
    """Test settings must be safe for CI."""

    def test_settings_no_debug_in_test(self):
        """DEBUG must be False in test mode."""
        self.assertFalse(
            settings.DEBUG,
            "DEBUG must be False in test settings",
        )
