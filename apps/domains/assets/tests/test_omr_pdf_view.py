from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.domains.assets.omr.renderer.html_renderer import OMRHtmlRenderer
from apps.domains.assets.omr.renderer.pdf_renderer import OMRPdfRenderer
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


class OMRDocumentRenderingTests(TestCase):
    def test_objective_only_document_renders_decorative_essay_area(self):
        doc = OMRDocument(exam_title="Exam", mc_count=20, essay_count=0)

        self.assertEqual(doc.essay_count, 0)
        self.assertEqual(doc.render_essay_count, 5)
        self.assertTrue(doc.has_decorative_essay_area)
        self.assertEqual(doc.render_essay_label, "단답형 공간")
        self.assertEqual(doc.to_defaults_dict()["essay_count"], 0)
        self.assertEqual(doc.to_defaults_dict()["render_essay_count"], 5)
        self.assertEqual(doc.to_defaults_dict()["render_essay_label"], "단답형 공간")

        html = OMRHtmlRenderer().render(doc).decode("utf-8")
        self.assertIn("객관식 1번 ~ 20번", html)
        self.assertIn("단답형 공간", html)
        self.assertNotIn("단답형 5문항", html)
        self.assertNotIn("서술형", html)

        pdf = OMRPdfRenderer().render(doc)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_real_essay_count_overrides_decorative_essay_area(self):
        doc = OMRDocument(exam_title="Exam", mc_count=20, essay_count=3)

        self.assertEqual(doc.render_essay_count, 3)
        html = OMRHtmlRenderer().render(doc).decode("utf-8")
        self.assertIn("단답형 3문항", html)
