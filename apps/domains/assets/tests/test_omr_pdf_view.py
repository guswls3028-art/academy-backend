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
from apps.domains.assets.omr.views.omr_document_views import ToolsOMRPreviewView
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

    def test_objective_only_document_can_hide_optional_essay_area(self):
        shown = OMRDocument(
            exam_title="Exam",
            mc_count=30,
            essay_count=0,
            include_optional_essay_area=True,
        )
        hidden = OMRDocument(
            exam_title="Exam",
            mc_count=30,
            essay_count=0,
            include_optional_essay_area=False,
        )

        self.assertEqual(shown.render_essay_count, 5)
        self.assertEqual(hidden.render_essay_count, 0)
        self.assertFalse(hidden.has_decorative_essay_area)
        self.assertIn("단답형 공간", OMRHtmlRenderer().render(shown).decode("utf-8"))
        self.assertNotIn("단답형 공간", OMRHtmlRenderer().render(hidden).decode("utf-8"))
        self.assertTrue(OMRPdfRenderer().render(hidden).startswith(b"%PDF"))

    def test_large_objective_only_document_hides_optional_essay_area_automatically(self):
        doc = OMRDocument(
            exam_title="Exam",
            mc_count=60,
            essay_count=0,
            include_optional_essay_area=True,
        )

        self.assertFalse(doc.can_include_optional_essay_area)
        self.assertEqual(doc.render_essay_count, 0)
        self.assertEqual(doc.validate(), [])
        self.assertNotIn("단답형 공간", OMRHtmlRenderer().render(doc).decode("utf-8"))
        self.assertTrue(OMRPdfRenderer().render(doc).startswith(b"%PDF"))

    def test_short_answer_only_document_supports_twenty_questions(self):
        doc = OMRDocument(exam_title="Exam", mc_count=0, essay_count=20)

        self.assertEqual(doc.render_essay_count, 20)
        self.assertEqual(doc.validate(), [])
        html = OMRHtmlRenderer().render(doc).decode("utf-8")
        self.assertNotIn("객관식 1번", html)
        self.assertIn("단답형 0~999 (백·십·일)", html)
        self.assertEqual(html.count('class="dr-place"'), 60)
        self.assertEqual(html.count('class="dr-bu"'), 600)
        self.assertTrue(OMRPdfRenderer().render(doc).startswith(b"%PDF"))

    def test_real_essay_count_overrides_decorative_essay_area(self):
        doc = OMRDocument(exam_title="Exam", mc_count=20, essay_count=3)

        self.assertEqual(doc.render_essay_count, 3)
        html = OMRHtmlRenderer().render(doc).decode("utf-8")
        self.assertIn("단답형 0~999 (백·십·일)", html)
        self.assertIn('<div class="dr-n">21</div>', html)
        self.assertIn('<div class="dr-n">23</div>', html)

    def test_mixed_question_order_renders_actual_numbers(self):
        doc = OMRDocument(
            exam_title="Mixed Exam",
            mc_count=2,
            essay_count=1,
            choice_question_numbers=(1, 3),
            essay_question_numbers=(2,),
        )

        self.assertEqual(doc.validate(), [])
        defaults = doc.to_defaults_dict()
        self.assertEqual(defaults["question_types"], ["choice", "essay", "choice"])
        html = OMRHtmlRenderer().render(doc).decode("utf-8")
        self.assertIn('<div class="ar-n">1</div>', html)
        self.assertIn('<div class="ar-n">3</div>', html)
        self.assertIn('<div class="dr-n">2</div>', html)
        self.assertTrue(OMRPdfRenderer().render(doc).startswith(b"%PDF"))


class OMRDocumentApiContractTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="omr-document-api",
            name="OMR Document API",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="omr-document-api-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")

    def _post_preview(self, payload: dict):
        request = self.factory.post(
            "/api/v1/tools/omr/preview/",
            payload,
            format="json",
        )
        force_authenticate(request, user=self.user)
        request.tenant = self.tenant
        return ToolsOMRPreviewView.as_view()(request)

    def test_preview_contract_hides_optional_essay_area(self):
        response = self._post_preview({
            "exam_title": "Objective only",
            "mc_count": 30,
            "essay_count": 0,
            "n_choices": 5,
            "include_optional_essay_area": False,
        })

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("단답형 공간", response.content.decode("utf-8"))

    def test_preview_contract_accepts_twenty_short_answer_questions(self):
        response = self._post_preview({
            "exam_title": "Short answer only",
            "mc_count": 0,
            "essay_count": 20,
            "n_choices": 5,
            "include_optional_essay_area": False,
        })

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("단답형 0~999 (백·십·일)", html)
        self.assertNotIn("객관식 1번", html)

    def test_preview_contract_accepts_mixed_question_number_order(self):
        response = self._post_preview({
            "exam_title": "Mixed order",
            "mc_count": 2,
            "essay_count": 1,
            "n_choices": 5,
            "include_optional_essay_area": False,
            "choice_question_numbers": [1, 3],
            "essay_question_numbers": [2],
        })

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn('<div class="ar-n">1</div>', html)
        self.assertIn('<div class="ar-n">3</div>', html)
        self.assertIn('<div class="dr-n">2</div>', html)

    def test_preview_contract_rejects_invalid_counts_instead_of_clamping(self):
        for payload in (
            {"mc_count": "abc", "essay_count": 0, "n_choices": 5},
            {"mc_count": 61, "essay_count": 0, "n_choices": 5},
            {"mc_count": 0, "essay_count": 21, "n_choices": 5},
            {"mc_count": None, "essay_count": 0, "n_choices": 5},
            {"mc_count": [], "essay_count": 0, "n_choices": 5},
            {"mc_count": 1.5, "essay_count": 0, "n_choices": 5},
            {"mc_count": True, "essay_count": 0, "n_choices": 5},
        ):
            with self.subTest(payload=payload):
                response = self._post_preview(payload)
                self.assertEqual(response.status_code, 400)

    def test_preview_contract_rejects_invalid_boolean_and_choice_count(self):
        for payload in (
            {
                "mc_count": 30,
                "essay_count": 0,
                "n_choices": 5,
                "include_optional_essay_area": "definitely",
            },
            {
                "mc_count": 30,
                "essay_count": 0,
                "n_choices": 5,
                "include_optional_essay_area": 2,
            },
            {
                "mc_count": 30,
                "essay_count": 0,
                "n_choices": 5,
                "include_optional_essay_area": None,
            },
            {"mc_count": 30, "essay_count": 0, "n_choices": 4},
        ):
            with self.subTest(payload=payload):
                response = self._post_preview(payload)
                self.assertEqual(response.status_code, 400)
