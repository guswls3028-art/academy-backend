from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.views.admin_session_exams_view import AdminSessionExamsView


User = get_user_model()
Exam = apps.get_model("exams", "Exam")
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")


class AdminSessionExamsViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Session Exams",
            code="sessionexams",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="sessionexams-admin",
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

    def _list(self):
        request = self.factory.get(f"/results/admin/sessions/{self.session.id}/exams/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return AdminSessionExamsView.as_view()(request, session_id=self.session.id)

    def test_orders_by_display_order_to_match_scores_tab(self):
        later = Exam.objects.create(
            tenant=self.tenant,
            title="나중 시험",
            exam_type=Exam.ExamType.REGULAR,
            display_order=20,
        )
        earlier = Exam.objects.create(
            tenant=self.tenant,
            title="먼저 시험",
            exam_type=Exam.ExamType.REGULAR,
            display_order=10,
        )
        later.sessions.add(self.session)
        earlier.sessions.add(self.session)

        response = self._list()

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["exam_id"] for row in response.data], [earlier.id, later.id])
        self.assertEqual(response.data[0]["display_order"], 10)

    def test_ties_use_created_at_then_id_to_match_scores_tab(self):
        later = Exam.objects.create(
            tenant=self.tenant,
            title="같은 순서 나중",
            exam_type=Exam.ExamType.REGULAR,
            display_order=0,
        )
        earlier = Exam.objects.create(
            tenant=self.tenant,
            title="같은 순서 먼저",
            exam_type=Exam.ExamType.REGULAR,
            display_order=0,
        )
        later.sessions.add(self.session)
        earlier.sessions.add(self.session)
        now = timezone.now()
        Exam.objects.filter(id=later.id).update(created_at=now)
        Exam.objects.filter(id=earlier.id).update(created_at=now - timedelta(minutes=1))

        response = self._list()

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["exam_id"] for row in response.data], [earlier.id, later.id])

    def test_excludes_cross_tenant_exam_even_if_m2m_is_contaminated(self):
        other_tenant = Tenant.objects.create(
            name="Other Tenant",
            code="sessionexams-other",
            is_active=True,
        )
        own_exam = Exam.objects.create(
            tenant=self.tenant,
            title="우리 시험",
            exam_type=Exam.ExamType.REGULAR,
            display_order=1,
        )
        foreign_exam = Exam.objects.create(
            tenant=other_tenant,
            title="타 테넌트 시험",
            exam_type=Exam.ExamType.REGULAR,
            display_order=2,
        )
        own_exam.sessions.add(self.session)
        foreign_exam.sessions.add(self.session)

        response = self._list()

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["exam_id"] for row in response.data], [own_exam.id])

    def test_excludes_inactive_and_template_exam_links(self):
        active_exam = Exam.objects.create(
            tenant=self.tenant,
            title="운영 시험",
            exam_type=Exam.ExamType.REGULAR,
            is_active=True,
            display_order=1,
        )
        inactive_exam = Exam.objects.create(
            tenant=self.tenant,
            title="삭제된 시험",
            exam_type=Exam.ExamType.REGULAR,
            is_active=False,
            display_order=2,
        )
        template_exam = Exam.objects.create(
            tenant=self.tenant,
            title="양식 시험",
            subject="MATH",
            exam_type=Exam.ExamType.TEMPLATE,
            is_active=True,
            display_order=3,
        )
        active_exam.sessions.add(self.session)
        inactive_exam.sessions.add(self.session)
        template_exam.sessions.add(self.session)

        response = self._list()

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["exam_id"] for row in response.data], [active_exam.id])
