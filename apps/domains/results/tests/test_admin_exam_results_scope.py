from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.results.models import Result
from apps.domains.results.views.admin_exam_results_view import AdminExamResultsView


User = get_user_model()


class AdminExamResultsScopeTest(TestCase, ClinicTestMixin):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.data = self.setup_full_tenant("admin-exam-results-scope", student_count=1)
        self.tenant = self.data["tenant"]
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]
        self.Exam = django_apps.get_model("exams", "Exam")
        self.admin_user = User.objects.create_user(
            username="admin_exam_results_scope_admin",
            password="test1234",
            is_staff=True,
            is_superuser=True,
        )
        if hasattr(self.admin_user, "tenant_id"):
            self.admin_user.tenant_id = self.tenant.id
            self.admin_user.save(update_fields=["tenant_id"])

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
        other = self.setup_full_tenant("admin-exam-results-scope-other", student_count=1)
        other_enrollment = other["enrollments"][0]
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
