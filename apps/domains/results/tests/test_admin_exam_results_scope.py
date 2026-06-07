from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models import Result
from apps.domains.results.views.admin_exam_results_view import AdminExamResultsView


User = get_user_model()


class AdminExamResultsScopeTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Admin Exam Results Scope",
            code="admin-exam-results-scope",
            is_active=True,
        )
        self.Lecture = django_apps.get_model("lectures", "Lecture")
        self.Session = django_apps.get_model("lectures", "Session")
        self.Student = django_apps.get_model("students", "Student")
        self.Enrollment = django_apps.get_model("enrollment", "Enrollment")
        self.Exam = django_apps.get_model("exams", "Exam")
        self.admin_user = User.objects.create_user(
            username="admin_exam_results_scope_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
            is_superuser=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin_user, role="admin")
        self.lecture = self.Lecture.objects.create(
            tenant=self.tenant,
            title="Scope Lecture",
            name="Scope Lecture",
            subject="MATH",
        )
        self.lec_session = self.Session.objects.create(lecture=self.lecture, order=1, title="1회차")
        self.enrollment = self._make_enrollment(self.tenant, self.lecture, "SCOPE001", "범위 학생")

    def _make_enrollment(self, tenant, lecture, ps_number: str, name: str):
        user = User.objects.create_user(
            username=f"{tenant.code}_{ps_number}",
            password="test1234",
            tenant=tenant,
        )
        student = self.Student.objects.create(
            tenant=tenant,
            user=user,
            ps_number=ps_number,
            omr_code=ps_number[-8:],
            name=name,
            parent_phone="01000000000",
        )
        return self.Enrollment.objects.create(
            tenant=tenant,
            student=student,
            lecture=lecture,
            status="ACTIVE",
        )

    def _make_exam(self, title="scope exam"):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title=title,
            pass_score=60,
            max_score=100,
        )
        exam.sessions.add(self.lec_session)
        return exam

    def _get(self, exam_id: int):
        request = self.factory.get(f"/results/admin/exams/{exam_id}/results/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin_user)
        return AdminExamResultsView.as_view()(request, exam_id=exam_id)

    def test_null_enrollment_result_is_ignored_without_500(self):
        exam = self._make_exam()
        Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=None,
            total_score=10,
            max_score=100,
        )
        Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=80,
            max_score=100,
        )

        response = self._get(exam.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["enrollment_id"], self.enrollment.id)

    def test_cross_tenant_enrollment_result_is_ignored(self):
        exam = self._make_exam()
        other_tenant = Tenant.objects.create(
            name="Admin Exam Results Scope Other",
            code="admin-exam-results-scope-other",
            is_active=True,
        )
        other_lecture = self.Lecture.objects.create(
            tenant=other_tenant,
            title="Other Lecture",
            name="Other Lecture",
            subject="MATH",
        )
        other_enrollment = self._make_enrollment(other_tenant, other_lecture, "OTHER001", "타 테넌트 학생")
        Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=other_enrollment,
            total_score=100,
            max_score=100,
        )
        Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=80,
            max_score=100,
        )

        response = self._get(exam.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["enrollment_id"], self.enrollment.id)
