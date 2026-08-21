from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import Exam
from apps.domains.lectures.models import Lecture, Session
from apps.domains.exams.views.exam_view import ExamViewSet


User = get_user_model()


class ExamListPaginationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Exam List Pagination",
            code="exam-list-pagination",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="exam-list-pagination-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )

    def test_requested_page_size_returns_exams_beyond_default_twenty(self):
        for index in range(25):
            Exam.objects.create(
                tenant=self.tenant,
                title=f"Template {index:02d}",
                subject="MATH",
                exam_type=Exam.ExamType.TEMPLATE,
            )

        request = self.factory.get(
            "/api/v1/exams/?exam_type=template&page_size=100"
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = ExamViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 25)
        self.assertEqual(len(response.data["results"]), 25)
        self.assertEqual(response.data["results"][0]["title"], "Template 24")

    def test_lecture_filter_does_not_duplicate_exam_linked_to_two_sessions(self):
        lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="중복 방지 강의",
            name="중복 방지 강의",
            subject="MATH",
        )
        first_session = Session.objects.create(
            lecture=lecture,
            order=1,
            title="1회차",
        )
        second_session = Session.objects.create(
            lecture=lecture,
            order=2,
            title="2회차",
        )
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="공통 시험",
            exam_type=Exam.ExamType.REGULAR,
        )
        exam.sessions.add(first_session, second_session)

        request = self.factory.get(
            f"/api/v1/exams/?lecture_id={lecture.id}&page_size=100"
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = ExamViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            [row["id"] for row in response.data["results"]],
            [exam.id],
        )

    def test_lecture_filter_fails_closed_for_cross_tenant_session_link(self):
        foreign_tenant = Tenant.objects.create(
            code="foreign-exam-filter",
            name="Foreign exam filter tenant",
        )
        foreign_lecture = Lecture.objects.create(
            tenant=foreign_tenant,
            title="외부 강의",
            name="외부 강의",
            subject="MATH",
        )
        foreign_session = Session.objects.create(
            lecture=foreign_lecture,
            order=1,
            title="외부 1회차",
        )
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="잘못 연결된 내부 시험",
            exam_type=Exam.ExamType.REGULAR,
        )
        exam.sessions.add(foreign_session)

        request = self.factory.get(
            f"/api/v1/exams/?lecture_id={foreign_lecture.id}&page_size=100"
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = ExamViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])
