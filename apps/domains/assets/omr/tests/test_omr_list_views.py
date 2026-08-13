from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.assets.omr.views.omr_list_views import (
    ObjectiveOMRMetaView,
    ObjectiveOMRTemplateListView,
)


User = get_user_model()


class ObjectiveOMRQueryValidationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="OMR Query Tenant",
            code="omr-query",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="omr_query_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )

    def test_meta_rejects_non_integer_essay_count(self):
        request = self.factory.get(
            "/api/v1/assets/omr/objective/meta/",
            {"question_count": 20, "essay_count": "not-a-number"},
        )
        force_authenticate(request, user=self.admin)

        response = ObjectiveOMRMetaView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("essay_count", response.data)

    def test_meta_rejects_unsupported_choice_count(self):
        request = self.factory.get(
            "/api/v1/assets/omr/objective/meta/",
            {"question_count": 20, "n_choices": 4},
        )
        force_authenticate(request, user=self.admin)

        response = ObjectiveOMRMetaView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("n_choices", response.data)

    def test_template_list_rejects_non_integer_exam_id(self):
        request = self.factory.get(
            "/api/v1/assets/omr/objective/",
            {"exam_id": "not-a-number"},
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = ObjectiveOMRTemplateListView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("exam_id", response.data)
