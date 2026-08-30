from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import Exam, Sheet
from apps.domains.exams.views.omr_generate_view import GenerateOMRSheetAssetView


User = get_user_model()


class LegacyOMRGenerateViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Legacy OMR Tenant",
            code="legacy-omr",
            is_active=True,
        )
        self.other_tenant = Tenant.objects.create(
            name="Other Legacy OMR Tenant",
            code="other-legacy-omr",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="legacy_omr_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        TenantMembership.ensure_active(
            tenant=self.other_tenant,
            user=self.admin,
            role="admin",
        )
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="Sessionless Essay Exam",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        Sheet.objects.create(
            exam=self.exam,
            name="MAIN",
            total_questions=20,
            choice_count=0,
            essay_count=20,
        )

    def _post(self, data: dict, *, tenant: Tenant | None = None):
        request = self.factory.post(
            f"/api/v1/exams/{self.exam.id}/generate-omr/",
            data,
            format="json",
        )
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=self.admin)
        return GenerateOMRSheetAssetView.as_view()(request, exam_id=self.exam.id)

    def test_explicit_zero_choice_count_survives_for_sessionless_tenant_exam(self):
        response = self._post({"mc_count": 0, "essay_count": 20, "n_choices": 5})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["mc_count"], 0)
        self.assertEqual(response.data["essay_count"], 20)
        self.assertIn("mc=0", response.data["omr_url"])
        self.assertEqual(response.data["meta"]["essay_count"], 0)
        self.assertEqual(response.data["meta"]["numeric_short_answers"], [])

    def test_invalid_count_returns_validation_error_instead_of_server_error(self):
        response = self._post({"mc_count": "not-a-number", "essay_count": 0})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn("mc_count", response.data)

    def test_other_tenant_cannot_generate_exam_asset(self):
        response = self._post(
            {"mc_count": 0, "essay_count": 20},
            tenant=self.other_tenant,
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
