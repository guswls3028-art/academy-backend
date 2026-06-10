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
