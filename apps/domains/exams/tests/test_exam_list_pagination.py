from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import Exam
from apps.domains.exams.views.exam_view import ExamViewSet


User = get_user_model()


class ExamListPaginationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Exam List Pagination",
            code="exam-list-pagination",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="exam-list-pagination-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )

    def test_requested_page_size_returns_exams_beyond_default_twenty(self):
        for index in range(25):
            Exam.objects.create(
                tenant=self.tenant,
                title=f"Template {index:02d}",
                subject="MATH",
                exam_type=Exam.ExamType.TEMPLATE,
            )

        request = self.factory.get(
            "/api/v1/exams/?exam_type=template&page_size=100"
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = ExamViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 25)
        self.assertEqual(len(response.data["results"]), 25)
        self.assertEqual(response.data["results"][0]["title"], "Template 24")
