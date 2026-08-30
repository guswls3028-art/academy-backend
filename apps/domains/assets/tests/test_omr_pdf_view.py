from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.models.program import Program
from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.domains.assets.omr.renderer.html_renderer import OMRHtmlRenderer
from apps.domains.assets.omr.renderer.pdf_renderer import OMRPdfRenderer
from apps.domains.assets.omr.services.omr_document_service import OMRDocumentService
from apps.domains.assets.omr.views.omr_pdf_views import OMRPdfView
from apps.domains.assets.omr.views.omr_document_views import ToolsOMRPreviewView
from apps.domains.exams.models import AnswerKey, Exam, ExamAsset, ExamQuestion, Sheet
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

    def test_exam_answer_key_renders_non_objective_answer_as_written_area(self):
        sheet = Sheet.objects.create(
            exam=self.exam,
            total_questions=20,
            choice_count=19,
            essay_count=1,
        )
        questions = [
            ExamQuestion.objects.create(
                sheet=sheet,
                number=number,
                question_kind="choice" if number < 20 else "essay",
            )
            for number in range(1, 21)
        ]
        answer_key = AnswerKey.objects.create(
            exam=self.exam,
            answers={
                **{str(question.id): "1" for question in questions[:-1]},
                str(questions[-1].id): "풀이 과정을 서술하세요",
            },
        )

        written_doc = OMRDocumentService.from_exam(
            exam=self.exam,
            tenant=self.tenant,
            mc_count=19,
            essay_count=1,
            choice_question_numbers=list(range(1, 20)),
            essay_question_numbers=[20],
        )
        written_html = OMRHtmlRenderer().render(written_doc).decode("utf-8")

        self.assertIn("서술형 1문항", written_html)
        self.assertEqual(written_html.count('class="dr-bx"'), 1)
        self.assertNotIn('class="dr-place"', written_html)

        answer_key.answers[str(questions[-1].id)] = "7"
        answer_key.save(update_fields=["answers"])
        numeric_doc = OMRDocumentService.from_exam(
            exam=self.exam,
            tenant=self.tenant,
            mc_count=19,
            essay_count=1,
            choice_question_numbers=list(range(1, 20)),
            essay_question_numbers=[20],
        )
        numeric_html = OMRHtmlRenderer().render(numeric_doc).decode("utf-8")

        self.assertIn("서술형 1문항", numeric_html)
        self.assertEqual(numeric_html.count('class="dr-bx"'), 1)
        self.assertNotIn('class="dr-place"', numeric_html)


class OMRDocumentRenderingTests(TestCase):
    @patch("apps.infrastructure.storage.r2.get_admin_object_bytes")
    def test_pdf_uses_same_uploaded_logo_object_as_html_preview(self, get_logo_bytes):
        get_logo_bytes.return_value = (b"uploaded-logo", "image/webp")
        doc = OMRDocument(
            exam_title="Exam",
            logo_url="https://signed.example.test/tenant-logo.webp",
            logo_key="tenant-logos/1/logo.webp",
        )

        html = OMRHtmlRenderer().render(doc).decode("utf-8")
        pdf_doc = OMRDocumentService.fetch_logo_bytes(doc, tenant=self)

        self.assertIn(doc.logo_url, html)
        self.assertEqual(pdf_doc.logo_bytes, b"uploaded-logo")
        self.assertEqual(pdf_doc.logo_mime, "image/webp")
        get_logo_bytes.assert_called_once_with(
            key="tenant-logos/1/logo.webp",
            max_bytes=5 * 1024 * 1024,
            timeout_seconds=5,
        )

    def test_objective_only_document_renders_decorative_essay_area(self):
        doc = OMRDocument(exam_title="Exam", mc_count=20, essay_count=0)

        self.assertEqual(doc.essay_count, 0)
        self.assertEqual(doc.render_essay_count, 5)
        self.assertTrue(doc.has_decorative_essay_area)
        self.assertEqual(doc.render_essay_label, "서술형 작성 공간")
        self.assertEqual(doc.to_defaults_dict()["essay_count"], 0)
        self.assertEqual(doc.to_defaults_dict()["render_essay_count"], 5)
        self.assertEqual(doc.to_defaults_dict()["render_essay_label"], "서술형 작성 공간")

        html = OMRHtmlRenderer().render(doc).decode("utf-8")
        self.assertIn("객관식 1번 ~ 20번", html)
        self.assertIn("서술형 작성 공간", html)
        self.assertNotIn("단답형", html)

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
        self.assertIn("서술형 작성 공간", OMRHtmlRenderer().render(shown).decode("utf-8"))
        self.assertNotIn("서술형 작성 공간", OMRHtmlRenderer().render(hidden).decode("utf-8"))
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
        self.assertNotIn("서술형 작성 공간", OMRHtmlRenderer().render(doc).decode("utf-8"))
        self.assertTrue(OMRPdfRenderer().render(doc).startswith(b"%PDF"))

    def test_written_answer_only_document_supports_twenty_questions(self):
        doc = OMRDocument(exam_title="Exam", mc_count=0, essay_count=20)

        self.assertEqual(doc.render_essay_count, 20)
        self.assertEqual(doc.validate(), [])
        html = OMRHtmlRenderer().render(doc).decode("utf-8")
        self.assertNotIn("객관식 1번", html)
        self.assertIn("서술형 20문항", html)
        self.assertNotIn("단답형 0~999 (백·십·일)", html)
        self.assertEqual(html.count('class="dr-bx"'), 20)
        self.assertTrue(OMRPdfRenderer().render(doc).startswith(b"%PDF"))

    def test_real_essay_count_overrides_decorative_essay_area(self):
        doc = OMRDocument(exam_title="Exam", mc_count=20, essay_count=3)

        self.assertEqual(doc.render_essay_count, 3)
        html = OMRHtmlRenderer().render(doc).decode("utf-8")
        self.assertIn("서술형 3문항", html)
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

    def test_all_non_objective_rows_render_as_written_areas(self):
        doc = OMRDocument(
            exam_title="Mixed written answers",
            mc_count=19,
            essay_count=2,
            essay_question_numbers=(20, 21),
        )

        html = OMRHtmlRenderer().render(doc).decode("utf-8")

        self.assertIn("서술형 2문항", html)
        self.assertNotIn('class="dr-place"', html)
        self.assertEqual(html.count('class="dr-bx"'), 2)
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

    @patch("apps.infrastructure.storage.r2.resolve_admin_logo_url")
    def test_document_rejects_logo_key_outside_current_tenant(self, resolve_logo_url):
        program = Program.objects.get(tenant=self.tenant)
        program.ui_config = {
            "logo_key": f"tenant-logos/{self.tenant.id + 1}/logo.webp",
        }
        program.save(update_fields=["ui_config"])

        doc = OMRDocumentService.from_params(
            tenant=self.tenant,
            exam_title="Tenant scoped logo",
            mc_count=20,
            essay_count=0,
            n_choices=5,
        )

        self.assertIsNone(doc.logo_key)
        resolve_logo_url.assert_not_called()

    def test_preview_contract_hides_optional_essay_area(self):
        response = self._post_preview({
            "exam_title": "Objective only",
            "mc_count": 30,
            "essay_count": 0,
            "n_choices": 5,
            "include_optional_essay_area": False,
        })

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("서술형 작성 공간", response.content.decode("utf-8"))

    def test_tools_preview_renders_twenty_written_answers_without_numeric_bubbles(self):
        response = self._post_preview({
            "exam_title": "Written answer only",
            "mc_count": 0,
            "essay_count": 20,
            "n_choices": 5,
            "include_optional_essay_area": False,
        })

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("서술형 20문항", html)
        self.assertNotIn("단답형 0~999 (백·십·일)", html)
        self.assertNotIn('class="dr-place"', html)
        self.assertEqual(html.count('class="dr-bx"'), 20)
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
