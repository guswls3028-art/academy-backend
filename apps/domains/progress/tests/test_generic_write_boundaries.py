from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.progress.models import (
    LectureProgress,
    ProgressPolicy,
    RiskLog,
    SessionProgress,
)
from apps.domains.progress.views import (
    LectureProgressViewSet,
    ProgressPolicyViewSet,
    RiskLogViewSet,
    SessionProgressViewSet,
)


User = get_user_model()


class DerivedProgressApiReadOnlyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Derived Progress Read Only",
            code="derived-progress-read-only",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="derived_progress_read_only_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )

    def _request(self, method: str, path: str, data: dict | None = None):
        request = getattr(self.factory, method)(path, data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return request

    def test_generic_mutations_are_not_exposed_for_derived_state(self):
        cases = [
            ("policies", ProgressPolicyViewSet, ProgressPolicy),
            ("session-progress", SessionProgressViewSet, SessionProgress),
            ("lecture-progress", LectureProgressViewSet, LectureProgress),
            ("risk-logs", RiskLogViewSet, RiskLog),
        ]

        for route, viewset, model in cases:
            for action in ("create", "update", "partial_update", "destroy"):
                self.assertFalse(hasattr(viewset, action))

            with self.subTest(route=route, method="POST"):
                response = viewset.as_view({"get": "list"})(
                    self._request(
                        "post",
                        f"/api/v1/progress/{route}/",
                        {"lecture": 999, "session": 999, "enrollment": 999},
                    )
                )
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
                self.assertFalse(model.objects.exists())

            with self.subTest(route=route, method="PATCH"):
                response = viewset.as_view({"get": "retrieve"})(
                    self._request(
                        "patch",
                        f"/api/v1/progress/{route}/999/",
                        {"lecture": 999, "session": 999},
                    ),
                    pk=999,
                )
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

            with self.subTest(route=route, method="DELETE"):
                response = viewset.as_view({"get": "retrieve"})(
                    self._request("delete", f"/api/v1/progress/{route}/999/"),
                    pk=999,
                )
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
