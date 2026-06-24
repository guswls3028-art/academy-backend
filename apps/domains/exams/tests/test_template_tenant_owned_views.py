from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import AnswerKey, Exam, ExamQuestion, Sheet
from apps.domains.exams.views.exam_questions_by_exam_view import ExamQuestionsByExamView
from apps.domains.exams.views.question_auto_view import SheetAutoQuestionsView
from apps.domains.exams.views.question_explanation_view import (
    ExamExplanationListView,
    QuestionExplanationDetailView,
)
from apps.domains.exams.views.sheet_view import SheetViewSet
from apps.domains.exams.views.template_builder_view import TemplateBuilderView
from apps.domains.exams.views.template_editor_view import TemplateEditorView
from apps.domains.exams.views.template_status_view import TemplateStatusView
from apps.domains.exams.views.template_validation_view import TemplateValidationView


User = get_user_model()


class TenantOwnedTemplateViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Tenant Owned Template",
            code="tenant-owned-template",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="tenant_template_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")

    def _auth(self, request, *, tenant: Tenant | None = None):
        force_authenticate(request, user=self.admin)
        if tenant is not None:
            request.tenant = tenant
        return request

    def _template(self) -> Exam:
        return Exam.objects.create(
            tenant=self.tenant,
            title="Reusable Template",
            exam_type=Exam.ExamType.TEMPLATE,
            subject="math",
        )

    def test_template_lifecycle_views_accept_tenant_owned_template_without_session(self):
        template = self._template()

        builder_request = self._auth(
            self.factory.post(f"/api/v1/exams/{template.id}/builder/", {}, format="json"),
            tenant=self.tenant,
        )
        builder_response = TemplateBuilderView.as_view()(builder_request, exam_id=template.id)

        self.assertEqual(builder_response.status_code, status.HTTP_200_OK, builder_response.data)
        sheet = Sheet.objects.get(exam=template)
        self.assertTrue(AnswerKey.objects.filter(exam=template).exists())
        self.assertEqual(builder_response.data["sheet_id"], sheet.id)

        editor_request = self._auth(
            self.factory.get(f"/api/v1/exams/{template.id}/template-editor/"),
            tenant=self.tenant,
        )
        editor_response = TemplateEditorView.as_view()(editor_request, exam_id=template.id)
        self.assertEqual(editor_response.status_code, status.HTTP_200_OK, editor_response.data)
        self.assertEqual(editor_response.data["sheet_id"], sheet.id)

        status_request = self._auth(
            self.factory.get(f"/api/v1/exams/{template.id}/template-status/"),
            tenant=self.tenant,
        )
        status_response = TemplateStatusView.as_view()(status_request, exam_id=template.id)
        self.assertEqual(status_response.status_code, status.HTTP_200_OK, status_response.data)
        self.assertTrue(status_response.data["has_sheet"])
        self.assertEqual(status_response.data["question_count"], 0)

        validation_request = self._auth(
            self.factory.get(f"/api/v1/exams/{template.id}/template-validation/"),
            tenant=self.tenant,
        )
        validation_response = TemplateValidationView.as_view()(validation_request, exam_id=template.id)
        self.assertEqual(validation_response.status_code, status.HTTP_200_OK, validation_response.data)
        self.assertEqual(validation_response.data["reason"], "NO_QUESTIONS")

    def test_sheet_and_auto_questions_accept_tenant_owned_template_without_session(self):
        template = self._template()
        create_sheet_request = self._auth(
            self.factory.post(
                "/api/v1/exams/sheets/",
                {"exam": template.id, "name": "MAIN", "total_questions": 0},
                format="json",
            ),
            tenant=self.tenant,
        )
        create_sheet_response = SheetViewSet.as_view({"post": "create"})(create_sheet_request)

        self.assertEqual(create_sheet_response.status_code, status.HTTP_201_CREATED, create_sheet_response.data)
        sheet = Sheet.objects.get(exam=template)

        auto_request = self._auth(
            self.factory.post(
                f"/api/v1/exams/sheets/{sheet.id}/auto-questions/",
                {"boxes": [[1, 2, 30, 40], [5, 6, 35, 45]]},
                format="json",
            ),
            tenant=self.tenant,
        )
        auto_response = SheetAutoQuestionsView.as_view()(auto_request, sheet_id=sheet.id)

        self.assertEqual(auto_response.status_code, status.HTTP_200_OK, auto_response.data)
        self.assertEqual(
            list(ExamQuestion.objects.filter(sheet=sheet).order_by("number").values_list("number", flat=True)),
            [1, 2],
        )

    def test_tenant_scoped_read_views_reject_missing_tenant_before_body(self):
        template = self._template()
        sheet = Sheet.objects.create(exam=template, name="MAIN", total_questions=1)
        question = ExamQuestion.objects.create(sheet=sheet, number=1, score=1)

        cases = [
            (
                "template-status",
                self.factory.get(f"/api/v1/exams/{template.id}/template-status/"),
                TemplateStatusView.as_view(),
                {"exam_id": template.id},
            ),
            (
                "template-validation",
                self.factory.get(f"/api/v1/exams/{template.id}/template-validation/"),
                TemplateValidationView.as_view(),
                {"exam_id": template.id},
            ),
            (
                "exam-questions",
                self.factory.get(f"/api/v1/exams/{template.id}/questions/"),
                ExamQuestionsByExamView.as_view(),
                {"exam_id": template.id},
            ),
            (
                "explanation-list",
                self.factory.get(f"/api/v1/exams/{template.id}/explanations/"),
                ExamExplanationListView.as_view(),
                {"exam_id": template.id},
            ),
            (
                "explanation-detail",
                self.factory.get(f"/api/v1/exams/questions/{question.id}/explanation/"),
                QuestionExplanationDetailView.as_view(),
                {"question_id": question.id},
            ),
        ]

        for label, request, view, kwargs in cases:
            with self.subTest(label=label):
                response = view(self._auth(request), **kwargs)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
