from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
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

    def _create_exam(self, title="새 시험"):
        request = self.factory.post(
            "/exams/",
            {
                "title": title,
                "exam_type": Exam.ExamType.REGULAR,
                "session_id": self.session.id,
                "max_score": 80,
                "pass_score": 64,
                "answer_visibility": Exam.AnswerVisibility.AFTER_CLOSED,
            },
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
