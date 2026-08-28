from __future__ import annotations

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import Exam, ExamQuestion, Sheet
from apps.domains.exams.views.question_view import QuestionViewSet


User = get_user_model()
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")


class QuestionViewSetStructureOwnerTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Question View Tenant",
            code="question-view",
            is_active=True,
        )
        self.other_tenant = Tenant.objects.create(
            name="Other Question Tenant",
            code="question-view-other",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="question_view_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Question View Lecture",
            name="Question View Lecture",
            subject="SCIENCE",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1회",
        )

    def _patch_question(self, question: ExamQuestion, data: dict, *, tenant: Tenant | None = None):
        request = self.factory.patch(
            f"/api/v1/exams/questions/{question.id}/",
            data,
            format="json",
        )
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=self.admin)
        view = QuestionViewSet.as_view({"patch": "partial_update"})
        return view(request, pk=question.id)

    def _create_question(self, data: dict):
        request = self.factory.post(
            "/api/v1/exams/questions/",
            data,
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return QuestionViewSet.as_view({"post": "create"})(request)

    def test_regular_without_template_can_update_own_question_score(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Regular Own Structure",
            exam_type=Exam.ExamType.REGULAR,
        )
        exam.sessions.add(self.session)
        sheet = Sheet.objects.create(exam=exam, name="MAIN", total_questions=1)
        question = ExamQuestion.objects.create(sheet=sheet, number=1, score=3)

        response = self._patch_question(question, {"score": 4.5})

        self.assertEqual(response.status_code, 200, response.data)
        question.refresh_from_db()
        self.assertEqual(question.score, 4.5)

    def test_unused_template_question_can_update_without_session(self):
        template = Exam.objects.create(
            tenant=self.tenant,
            title="Editable Template",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        sheet = Sheet.objects.create(exam=template, name="MAIN", total_questions=1)
        question = ExamQuestion.objects.create(sheet=sheet, number=1, score=3)

        response = self._patch_question(question, {"score": 5})

        self.assertEqual(response.status_code, 200, response.data)
        question.refresh_from_db()
        self.assertEqual(question.score, 5)

    def test_used_template_question_remains_locked(self):
        template = Exam.objects.create(
            tenant=self.tenant,
            title="Used Template",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        regular = Exam.objects.create(
            tenant=self.tenant,
            title="Regular From Template",
            exam_type=Exam.ExamType.REGULAR,
            template_exam=template,
        )
        regular.sessions.add(self.session)
        sheet = Sheet.objects.create(exam=template, name="MAIN", total_questions=1)
        question = ExamQuestion.objects.create(sheet=sheet, number=1, score=3)

        response = self._patch_question(question, {"score": 5})

        self.assertEqual(response.status_code, 400, response.data)
        question.refresh_from_db()
        self.assertEqual(question.score, 3)

    def test_cross_tenant_question_is_not_patchable(self):
        other_exam = Exam.objects.create(
            tenant=self.other_tenant,
            title="Other Tenant Exam",
            exam_type=Exam.ExamType.REGULAR,
        )
        other_sheet = Sheet.objects.create(exam=other_exam, name="MAIN", total_questions=1)
        other_question = ExamQuestion.objects.create(sheet=other_sheet, number=1, score=3)

        response = self._patch_question(other_question, {"score": 5})

        self.assertEqual(response.status_code, 404, response.data)
        other_question.refresh_from_db()
        self.assertEqual(other_question.score, 3)

    def test_question_create_keeps_same_tenant_sheet_writable(self):
        template = Exam.objects.create(
            tenant=self.tenant,
            title="Writable Question Template",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        sheet = Sheet.objects.create(
            exam=template,
            name="MAIN",
            total_questions=0,
        )

        response = self._create_question(
            {"sheet": sheet.id, "number": 1, "score": 3}
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["sheet"], sheet.id)
        self.assertTrue(ExamQuestion.objects.filter(sheet=sheet, number=1).exists())

    def test_question_create_hides_cross_tenant_sheet_existence(self):
        other_exam = Exam.objects.create(
            tenant=self.other_tenant,
            title="Other Tenant Template",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        other_sheet = Sheet.objects.create(
            exam=other_exam,
            name="MAIN",
            total_questions=0,
        )

        foreign_response = self._create_question(
            {"sheet": other_sheet.id, "number": 1, "score": 3}
        )
        missing_response = self._create_question(
            {"sheet": other_sheet.id + 1_000_000, "number": 1, "score": 3}
        )

        self.assertEqual(foreign_response.status_code, 400, foreign_response.data)
        self.assertEqual(missing_response.status_code, 400, missing_response.data)
        self.assertEqual(foreign_response.data, missing_response.data)
        self.assertFalse(ExamQuestion.objects.filter(sheet=other_sheet).exists())

    def test_question_update_cannot_move_owner_sheet(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Question Owner A",
            exam_type=Exam.ExamType.REGULAR,
        )
        exam.sessions.add(self.session)
        sheet = Sheet.objects.create(exam=exam, name="MAIN", total_questions=1)
        question = ExamQuestion.objects.create(sheet=sheet, number=1, score=3)
        other_exam = Exam.objects.create(
            tenant=self.other_tenant,
            title="Question Owner B",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        other_sheet = Sheet.objects.create(
            exam=other_exam,
            name="MAIN",
            total_questions=0,
        )

        response = self._patch_question(
            question,
            {"sheet": other_sheet.id, "score": 4.5},
        )

        self.assertEqual(response.status_code, 400, response.data)
        question.refresh_from_db()
        self.assertEqual(question.sheet_id, sheet.id)
        self.assertEqual(question.score, 3)
