from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.results.views.admin_student_grades_view import AdminStudentGradesView


User = get_user_model()


class AdminStudentGradesScopeTest(TestCase, ClinicTestMixin):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.data = self.setup_full_tenant("student-grades-scope", student_count=1)
        self.tenant = self.data["tenant"]
        self.student = self.data["students"][0]
        self.admin_user = User.objects.create_user(
            username="student_grades_scope_admin",
            password="test1234",
            is_staff=True,
            is_superuser=True,
        )
        if hasattr(self.admin_user, "tenant_id"):
            self.admin_user.tenant_id = self.tenant.id
            self.admin_user.save(update_fields=["tenant_id"])

    def _get(self, student_id):
        request = self.factory.get(
            "/results/admin/student-grades/",
            {"student_id": student_id},
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin_user)
        return AdminStudentGradesView.as_view()(request)

    def test_invalid_student_id_returns_400(self):
        response = self._get("abc")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "student_id must be integer")

    def test_active_same_tenant_student_returns_empty_payload_when_no_scores(self):
        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"exams": [], "homeworks": []})

    def test_cross_tenant_student_returns_404(self):
        other = self.setup_full_tenant("student-grades-other", student_count=1)
        other_student = other["students"][0]

        response = self._get(other_student.id)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["detail"], "student not found")

    def test_soft_deleted_same_tenant_student_returns_404(self):
        self.student.deleted_at = timezone.now()
        self.student.save(update_fields=["deleted_at"])

        response = self._get(self.student.id)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["detail"], "student not found")
