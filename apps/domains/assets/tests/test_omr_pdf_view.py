from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.assets.omr.views.omr_pdf_views import OMRPdfView
from apps.domains.exams.models import Exam, ExamAsset
from apps.domains.lectures.models import Lecture, Session


User = get_user_model()


class OMRPdfViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="omr-pdf", name="OMR PDF", is_active=True)
        self.user = User.objects.create_user(
            username="omr-pdf-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")
        lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Math",
            name="Math",
            subject="MATH",
        )
        session = Session.objects.create(lecture=lecture, order=1, title="S1")
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="Exam",
            exam_type=Exam.ExamType.REGULAR,
        )
        self.exam.sessions.add(session)

    def _request(self):
        request = self.factory.get("/api/v1/assets/omr/pdf/1/")
        force_authenticate(request, user=self.user)
        request.tenant = self.tenant
        return request

    @patch("apps.domains.assets.omr.views.omr_pdf_views.generate_presigned_get_url")
    def test_redirects_to_omr_asset_file_key(self, generate_url):
        generate_url.return_value = "https://example.test/omr.pdf"
        asset = ExamAsset.objects.create(
            exam=self.exam,
            asset_type=ExamAsset.AssetType.OMR_SHEET,
            file_key="tenants/1/exams/1/omr.pdf",
            file_type="application/pdf",
        )

        response = OMRPdfView.as_view()(self._request(), asset_id=asset.id)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://example.test/omr.pdf")
        generate_url.assert_called_once_with(
            key="tenants/1/exams/1/omr.pdf",
            expires_in=60 * 10,
        )

    @patch("apps.domains.assets.omr.views.omr_pdf_views.generate_presigned_get_url")
    def test_rejects_non_omr_asset_type(self, generate_url):
        asset = ExamAsset.objects.create(
            exam=self.exam,
            asset_type=ExamAsset.AssetType.PROBLEM_PDF,
            file_key="tenants/1/exams/1/problem.pdf",
            file_type="application/pdf",
        )

        response = OMRPdfView.as_view()(self._request(), asset_id=asset.id)

        self.assertEqual(response.status_code, 404)
        generate_url.assert_not_called()

