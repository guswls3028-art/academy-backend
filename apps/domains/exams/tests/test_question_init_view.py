from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import Exam, ExamQuestion, Sheet
from apps.domains.exams.views.exam_question_init_view import ExamQuestionInitView
from apps.domains.lectures.models import Lecture, Session


User = get_user_model()


class ExamQuestionInitViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Question Init Tenant",
            code="question-init",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="question_init_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Question Init Lecture",
            name="Question Init Lecture",
            subject="SCIENCE",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1회",
        )

    def _post(self, exam: Exam, data: dict):
        request = self.factory.post(
            f"/api/v1/exams/{exam.id}/questions/init/",
            data,
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return ExamQuestionInitView.as_view()(request, exam_id=exam.id)

    def _regular_exam_with_questions(self) -> tuple[Exam, Sheet]:
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Regular Structure Owner",
            exam_type=Exam.ExamType.REGULAR,
            max_score=100,
        )
        exam.sessions.add(self.session)
        sheet = Sheet.objects.create(
            exam=exam,
            name="MAIN",
            total_questions=3,
            choice_count=2,
            essay_count=1,
        )
        ExamQuestion.objects.create(sheet=sheet, number=1, score=3)
        ExamQuestion.objects.create(sheet=sheet, number=2, score=4)
        ExamQuestion.objects.create(sheet=sheet, number=3, score=30)
        return exam, sheet

    def test_count_only_shape_apply_preserves_existing_scores(self):
        exam, sheet = self._regular_exam_with_questions()

        response = self._post(exam, {"choice_count": 2, "essay_count": 1})

        self.assertEqual(response.status_code, 200, response.data)
        scores = list(
            ExamQuestion.objects.filter(sheet=sheet)
            .order_by("number")
            .values_list("score", flat=True)
        )
        self.assertEqual(scores, [3.0, 4.0, 30.0])

    def test_legacy_zero_score_shape_payload_does_not_clear_existing_scores(self):
        exam, sheet = self._regular_exam_with_questions()

        response = self._post(
            exam,
            {
                "choice_count": 2,
                "choice_score": 0,
                "essay_count": 1,
                "essay_score": 0,
            },
        )

        self.assertEqual(response.status_code, 200, response.data)
        scores = list(
            ExamQuestion.objects.filter(sheet=sheet)
            .order_by("number")
            .values_list("score", flat=True)
        )
        self.assertEqual(scores, [3.0, 4.0, 30.0])

    def test_explicit_non_zero_score_payload_updates_existing_scores(self):
        exam, sheet = self._regular_exam_with_questions()

        response = self._post(
            exam,
            {
                "choice_count": 2,
                "choice_score": 5,
                "essay_count": 1,
                "essay_score": 20,
            },
        )

        self.assertEqual(response.status_code, 200, response.data)
        scores = list(
            ExamQuestion.objects.filter(sheet=sheet)
            .order_by("number")
            .values_list("score", flat=True)
        )
        self.assertEqual(scores, [5.0, 5.0, 20.0])

    def test_template_bound_regular_does_not_edit_locked_template(self):
        template = Exam.objects.create(
            tenant=self.tenant,
            title="Locked Template",
            exam_type=Exam.ExamType.TEMPLATE,
            max_score=100,
        )
        regular = Exam.objects.create(
            tenant=self.tenant,
            title="Regular From Template",
            exam_type=Exam.ExamType.REGULAR,
            template_exam=template,
        )
        regular.sessions.add(self.session)

        response = self._post(regular, {"choice_count": 2, "essay_count": 1})

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Sheet.objects.filter(exam=regular).exists())
        self.assertFalse(Sheet.objects.filter(exam=template).exists())
