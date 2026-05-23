from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Lecture, Session
from apps.domains.student_app.results.views import MyGradesSummaryView
from apps.domains.students.models import Student


User = get_user_model()


class MyGradesSummaryHomeworkTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="student-grades-hw", name="Student Grades HW", is_active=True)
        self.user = User.objects.create_user(
            username="student-grades-hw-user",
            password="pw1234",
            tenant=self.tenant,
            name="학생",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.user,
            ps_number="SGH001",
            omr_code="11112222",
            name="학생",
            phone="01011112222",
            parent_phone="01033334444",
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="수학",
            name="수학",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1회",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )

    def _call(self):
        request = self.factory.get("/api/v1/student/grades/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return MyGradesSummaryView.as_view()(request)

    def test_assigned_unscored_homework_is_visible_as_not_submitted(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="미채점 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["homeworks"]), 1)
        row = response.data["homeworks"][0]
        self.assertEqual(row["homework_id"], homework.id)
        self.assertIsNone(row["score"])
        self.assertEqual(row["max_score"], 20.0)
        self.assertEqual(row["achievement"], "NOT_SUBMITTED")
        self.assertEqual(row["lecture_title"], "수학")

    def test_removed_homework_assignment_is_hidden_from_student_summary(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="제거된 과제",
            meta={"removed_from_session_at": "2026-05-24T00:00:00+09:00"},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["homeworks"], [])
