from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models import ExamAttempt
from apps.domains.results.views.exam_attempt_view import ExamAttemptViewSet


User = get_user_model()


class ExamAttemptApiReadOnlyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Exam Attempt Read Only",
            code="exam-attempt-read-only",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="exam_attempt_read_only_admin",
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

    def test_generic_attempt_mutations_are_not_exposed(self):
        for action in ("create", "update", "partial_update", "destroy"):
            self.assertFalse(hasattr(ExamAttemptViewSet, action))

        cases = [
            (
                ExamAttemptViewSet.as_view({"get": "list"}),
                self._request(
                    "post",
                    "/api/v1/results/exam-attempts/",
                    {"exam": 999, "enrollment": 999, "attempt_index": 1},
                ),
                {},
            ),
            (
                ExamAttemptViewSet.as_view({"get": "retrieve"}),
                self._request(
                    "patch",
                    "/api/v1/results/exam-attempts/999/",
                    {"status": "done"},
                ),
                {"pk": 999},
            ),
            (
                ExamAttemptViewSet.as_view({"get": "retrieve"}),
                self._request("delete", "/api/v1/results/exam-attempts/999/"),
                {"pk": 999},
            ),
        ]

        for view, request, kwargs in cases:
            with self.subTest(method=request.method):
                response = view(request, **kwargs)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_405_METHOD_NOT_ALLOWED,
                    response.data,
                )

        self.assertFalse(ExamAttempt.objects.exists())
