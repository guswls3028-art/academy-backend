from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import AnswerKey, ExamQuestion, Sheet
from apps.domains.exams.views.exam_view import ExamViewSet


User = get_user_model()
Exam = apps.get_model("exams", "Exam")
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")


class ExamCreateOrderTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Exam Create Order",
            code="examcreateorder",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="examcreateorder-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture",
            name="Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1회차")

    def _create_exam(self, title="새 시험", **overrides):
        payload = {
            "title": title,
            "exam_type": Exam.ExamType.REGULAR,
            "session_id": self.session.id,
            "max_score": 80,
            "pass_score": 64,
            "answer_visibility": Exam.AnswerVisibility.AFTER_CLOSED,
        }
        payload.update(overrides)
        request = self.factory.post(
            "/exams/",
            payload,
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return ExamViewSet.as_view({"post": "create"})(request)

    def test_regular_create_appends_after_existing_session_exams(self):
        first = Exam.objects.create(
            tenant=self.tenant,
            title="기존 시험",
            exam_type=Exam.ExamType.REGULAR,
            display_order=3,
        )
        first.sessions.add(self.session)

        response = self._create_exam()

        self.assertEqual(response.status_code, 201)
        created = Exam.objects.get(id=response.data["id"])
        self.assertEqual(created.display_order, 4)
        self.assertEqual(created.subject, "MATH")
        self.assertEqual(created.max_score, 80)
        self.assertEqual(created.pass_score, 64)
        self.assertEqual(created.answer_visibility, Exam.AnswerVisibility.AFTER_CLOSED)
        self.assertEqual(
            list(self.session.exams.order_by("display_order", "created_at", "id").values_list("id", flat=True)),
            [first.id, created.id],
        )

    def test_regular_create_from_source_copies_question_structure(self):
        source = Exam.objects.create(
            tenant=self.tenant,
            title="원본 시험",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            max_score=80,
            pass_score=64,
        )
        source_sheet = Sheet.objects.create(
            exam=source,
            name="MAIN",
            total_questions=2,
            choice_count=2,
            essay_count=0,
        )
        source_questions = [
            ExamQuestion.objects.create(sheet=source_sheet, number=1, score=30),
            ExamQuestion.objects.create(sheet=source_sheet, number=2, score=50),
        ]
        AnswerKey.objects.create(
            exam=source,
            answers={
                str(source_questions[0].id): "1",
                str(source_questions[1].id): "2",
            },
        )

        response = self._create_exam(
            title="복사된 시험",
            source_exam_id=source.id,
        )

        self.assertEqual(response.status_code, 201, response.data)
        copied = Exam.objects.get(id=response.data["id"])
        self.assertIsNone(copied.template_exam_id)
        copied_questions = list(copied.sheet.questions.order_by("number"))
        self.assertEqual(
            [(question.number, question.score) for question in copied_questions],
            [(1, 30), (2, 50)],
        )
        self.assertNotEqual(copied_questions[0].id, source_questions[0].id)
        self.assertEqual(
            copied.answer_key.answers,
            {
                str(copied_questions[0].id): "1",
                str(copied_questions[1].id): "2",
            },
        )

    def test_regular_create_rejects_cross_tenant_source(self):
        other_tenant = Tenant.objects.create(
            name="Other Exam Source",
            code="other-exam-source",
            is_active=True,
        )
        other_source = Exam.objects.create(
            tenant=other_tenant,
            title="다른 학원 시험",
            exam_type=Exam.ExamType.REGULAR,
        )

        response = self._create_exam(
            title="복사 차단 시험",
            source_exam_id=other_source.id,
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            Exam.objects.filter(tenant=self.tenant, title="복사 차단 시험").exists()
        )
