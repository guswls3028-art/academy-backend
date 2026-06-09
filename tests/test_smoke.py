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

import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase


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


class TestOMRLayoutSmoke(SimpleTestCase):
    """OMR column layout boundaries must stay shared by generated meta and preview data."""

    def test_omr_column_ranges_cover_layout_boundaries(self):
        from apps.domains.assets.omr.dto.omr_document import OMRDocument
        from apps.domains.assets.omr.renderer.html_renderer import OMRHtmlRenderer
        from apps.domains.assets.omr.services.meta_generator import (
            build_mc_column_ranges,
            build_omr_meta,
        )

        cases = {
            20: [(1, 20)],
            21: [(1, 11), (12, 21)],
            40: [(1, 20), (21, 40)],
            41: [(1, 14), (15, 28), (29, 41)],
            60: [(1, 20), (21, 40), (41, 60)],
        }

        renderer = OMRHtmlRenderer()
        for question_count, expected_ranges in cases.items():
            with self.subTest(question_count=question_count):
                ranges = [
                    (item["start"], item["end"])
                    for item in build_mc_column_ranges(question_count)
                ]
                meta = build_omr_meta(question_count=question_count, n_choices=5)
                meta_ranges = [
                    (
                        column["questions"][0]["question_number"],
                        column["questions"][-1]["question_number"],
                    )
                    for column in meta["columns"]
                ]
                html_ranges = [
                    (column["rows"][0]["number"], column["rows"][-1]["number"])
                    for column in renderer._build_mc_columns(  # noqa: SLF001
                        OMRDocument(exam_title="Smoke", mc_count=question_count)
                    )
                ]

                self.assertEqual(ranges, expected_ranges)
                self.assertEqual(meta_ranges, expected_ranges)
                self.assertEqual(html_ranges, expected_ranges)


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


class TestMatchupHitReportViewsImportable(TestCase):
    """매치업 적중보고서 view 모듈은 NameError 없이 import 되어야 한다.

    배경 (2026-05-10): D-9 분리 후 `views_hit_report.py` 에서 `MatchupProblem`
    module-level import 누락으로 학원장 보고서 진입 500 사고. 분리 직후 동일 패턴
    `_is_tenant_staff` 미정의 hotfix 한 번 더. 이 test 는 import 시점 NameError 와
    URL resolution 실패를 deploy gate 단계에서 차단한다.

    검증:
    - module import (NameError 발생 시 ImportError 로 fail)
    - 모든 hit-report 엔드포인트 URL resolution
    - View 클래스가 .as_view() 호출 가능 (module-level 의존성 모두 해소됨)
    """

    def test_views_hit_report_module_imports(self):
        from apps.domains.matchup import views_hit_report  # noqa: F401

    def test_hit_report_endpoints_resolve(self):
        from django.urls import resolve, Resolver404

        paths = [
            "/api/v1/matchup/documents/1/hit-report.pdf",
            "/api/v1/matchup/documents/1/hit-report-draft/",
            "/api/v1/matchup/hit-reports/",
            "/api/v1/matchup/hit-reports/1/",
            "/api/v1/matchup/hit-reports/1/entries/",
            "/api/v1/matchup/hit-reports/1/submit/",
            "/api/v1/matchup/hit-reports/1/curated.pdf",
            "/api/v1/matchup/hit-reports/1/share.zip",
        ]
        for path in paths:
            try:
                match = resolve(path)
            except Resolver404:
                self.fail(f"{path} does not resolve (404)")
            self.assertIsNotNone(match, f"{path} resolved to None")

    def test_hit_report_view_classes_callable(self):
        from apps.domains.matchup import views_hit_report

        view_classes = [
            "HitReportListView",
            "HitReportDraftView",
            "HitReportDetailView",
            "HitReportEntriesUpsertView",
            "HitReportSubmitView",
            "HitReportPdfView",
            "HitReportZipExportView",
            "DocumentHitReportPdfView",
        ]
        for name in view_classes:
            cls = getattr(views_hit_report, name, None)
            self.assertIsNotNone(cls, f"{name} not exported")
            view_fn = cls.as_view()
            self.assertTrue(callable(view_fn), f"{name}.as_view() not callable")


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

    def test_prod_ssl_redirect_default_is_proxy_safe(self):
        """Prod defaults must not self-redirect API clients behind Cloudflare/ALB."""
        env = os.environ.copy()
        env["SECRET_KEY"] = "test-production-secret-key-with-safe-length"
        env.pop("DJANGO_SECURE_SSL_REDIRECT", None)
        code = (
            "import apps.api.config.settings.prod as s; "
            "assert s.SECURE_SSL_REDIRECT is False; "
            "assert s.SESSION_COOKIE_SECURE is True; "
            "assert s.CSRF_COOKIE_SECURE is True"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
