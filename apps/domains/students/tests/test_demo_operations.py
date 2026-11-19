from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.authentication import _validate_dawon_demo_access
from apps.core.models import Tenant, TenantMembership
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam
from apps.domains.results.models import ExamAttempt, Result
from apps.domains.staffs.models import Staff
from apps.domains.students.models import ClassGroup, ClassGroupLecture, Student
from apps.domains.students.views.demo_operations import DawonDemoOperationsView
from apps.domains.students.views.demo_session import DawonDemoSessionView


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class DemoOperationsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(id=719, code="dawon", name="다원")
        self.other = Tenant.objects.create(code="other", name="다른 학원")
        self.user = get_user_model().objects.create_user(username="t719_dawon-demo-admin", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")
        employee = get_user_model().objects.create_user(username="t719_dawon-demo-employee", tenant=self.tenant)
        self.staff = Staff.objects.create(tenant=self.tenant, user=employee, name="다원 체험 강사", position="INSTRUCTOR")
        self.token = {"dawon_demo_role": "admin", "dawon_demo_until": int((timezone.now() + timedelta(hours=1)).timestamp())}

    def operation(self, method="get", data=None, tenant=None):
        path = "/api/v1/students/demo-operations/"
        request = getattr(APIRequestFactory(), method)(path, data, format="json")
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=self.user, token=self.token)
        return DawonDemoOperationsView.as_view()(request)

    def test_read_and_scoped_employee_position(self):
        course = Lecture.objects.create(tenant=self.tenant, title="수학", subject="수학")
        group = ClassGroup.objects.create(tenant=self.tenant, name="중등반", kind="union", track="SKY")
        ClassGroupLecture.objects.create(group=group, lecture=course, period="vacation")
        Lecture.objects.create(tenant=self.tenant, title="예비반 강의", subject="수학")
        Lecture.objects.create(tenant=self.other, title="다른 반", subject="수학")
        response = self.operation()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([g["name"] for g in response.data["groups"]], ["중등반", "미편성 강의"])
        self.assertEqual([g["courses"][0]["title"] for g in response.data["groups"]], ["수학", "예비반 강의"])
        self.assertEqual(response.data["employee"], {"name": "다원 체험 강사", "position": "INSTRUCTOR"})
        self.assertEqual(self.operation("patch", {"position": "ASSISTANT"}).status_code, 200)
        self.assertEqual(self.operation().data["employee"]["position"], "ASSISTANT")
        self.assertEqual(self.operation("patch", {"position": "ASSISTANT", "is_manager": True}).status_code, 400)
        self.assertEqual(self.operation(tenant=self.other).status_code, 404)
        self.token["dawon_demo_role"] = "teacher"
        self.assertEqual(self.operation().status_code, 404)

    def test_scores_count_confirmed_initial_results_only_once_per_enrollment(self):
        course = Lecture.objects.create(tenant=self.tenant, title="수학")
        lesson = Session.objects.create(lecture=course, order=1)
        exam = Exam.objects.create(tenant=self.tenant, title="중간")
        exam.sessions.add(lesson, Session.objects.create(lecture=course, order=2))
        for i, (value, meta) in enumerate([(0, {}), (80, {}), (0, {"status": "NOT_SUBMITTED"})]):
            user = get_user_model().objects.create_user(username=f"student-{i}", tenant=self.tenant)
            student = Student.objects.create(tenant=self.tenant, user=user, name=f"학생{i}",
                                             ps_number=f"qa-{i}", omr_code=f"{i:08}")
            enrollment = Enrollment.objects.create(tenant=self.tenant, lecture=course, student=student)
            attempt = ExamAttempt.objects.create(exam=exam, enrollment=enrollment, attempt_index=1, meta=meta)
            Result.objects.create(target_type="exam", target_id=exam.pk, enrollment=enrollment,
                                  attempt=attempt, total_score=value, max_score=100)
        self.assertEqual(self.operation().data["exams"], [
            {"id": exam.pk, "title": "중간", "graded": 2, "average": 40},
        ])

    def test_admin_token_issuance_and_api_allowlist(self):
        request = APIRequestFactory().post("/api/v1/students/demo-session/", {"role": "admin"}, format="json")
        request.tenant = self.tenant
        with patch.dict(AnonRateThrottle.THROTTLE_RATES, {"anon": "60/minute"}):
            response = DawonDemoSessionView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        token = AccessToken(response.data["access"])
        self.assertEqual(token["dawon_demo_role"], "admin")
        for path in ("/api/v1/staffs/", "/api/v1/core/student-support/session/", "/api/v1/fees/"):
            with self.subTest(path=path), self.assertRaises(PermissionDenied):
                read = APIRequestFactory().get(path)
                read.tenant = self.tenant
                _validate_dawon_demo_access(read, token)
