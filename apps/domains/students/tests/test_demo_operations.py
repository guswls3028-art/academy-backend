from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache.backends.locmem import LocMemCache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.authentication import _validate_dawon_demo_access
from apps.core.models import Tenant, TenantMembership
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models.homework import Homework
from apps.domains.homework_results.models.score import HomeworkScore
from apps.domains.submissions.models import Submission
from apps.domains.results.models import ExamAttempt, Result
from apps.domains.staffs.models import Staff
from apps.domains.students.models import ClassGroup, ClassGroupLecture, Student
from apps.domains.teachers.models import Teacher
from apps.domains.students.views.demo_operations import DawonDemoOperationsView
from apps.domains.students.views.demo_session import DawonDemoLearningThrottle, DawonDemoSessionView


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

    def test_course_detail_scopes_shared_exam_scores_and_homework(self):
        exam = Exam.objects.create(tenant=self.tenant, title="공통 시험")
        for i, score in enumerate((40, 80)):
            Teacher.objects.create(tenant=self.tenant, name=f"담당{i}")
            course = Lecture.objects.create(tenant=self.tenant, title=f"강의{i}", name=f"담당{i}")
            session = Session.objects.create(lecture=course, order=1, title=f"수업{i}")
            exam.sessions.add(session)
            homework = Homework.objects.create(tenant=self.tenant, session=session, title=f"과제{i}", status="OPEN")
            Homework.objects.create(tenant=self.other, session=session, title="다른 학원", status="OPEN")
            user = get_user_model().objects.create_user(username=f"learner{i}", tenant=self.tenant)
            student = Student.objects.create(tenant=self.tenant, user=user, name="학생", ps_number=f"qa-{i}", omr_code=f"{i:08}")
            enrollment = Enrollment.objects.create(tenant=self.tenant, lecture=course, student=student)
            HomeworkAssignment.objects.create(tenant=self.tenant, homework=homework, session=session, enrollment=enrollment)
            if i == 0:
                Submission.objects.create(tenant=self.tenant, user=user, enrollment=enrollment, target_type="homework",
                                          target_id=homework.pk, source="online", status="done")
                HomeworkScore.objects.create(homework=homework, session=session, enrollment=enrollment, score=0)
            attempt = ExamAttempt.objects.create(exam=exam, enrollment=enrollment, attempt_index=1)
            Result.objects.create(target_type="exam", target_id=exam.pk, enrollment=enrollment,
                                  attempt=attempt, total_score=score, max_score=100)
        data = self.operation().data
        self.assertEqual(data["exams"][0]["average"], 60)
        for i, course in enumerate(data["groups"][0]["courses"]):
            self.assertEqual(course["homework"][0]["progress"], {
                "assigned": 1, "submitted": 1 - i, "graded": 1 - i, "missing": i, "deferred": 0,
            })
            self.assertEqual(course["instructor"], f"담당{i}")
            self.assertEqual([s["title"] for s in course["sessions"]], [f"수업{i}"])
            self.assertEqual([(h["title"], h["status"]) for h in course["homework"]], [(f"과제{i}", "OPEN")])
            self.assertEqual(course["exams"], [{"id": exam.pk, "title": "공통 시험", "graded": 1, "average": (40, 80)[i]}])

    def test_instructor_uses_tenant_reference_or_recognised_legacy_name(self):
        reference = get_user_model().objects.create_user(username="qa-reference", tenant=self.tenant, name="현재 강사")
        foreign = get_user_model().objects.create_user(username="qa-foreign", tenant=self.other, name="타 학원 강사")
        Teacher.objects.create(tenant=self.tenant, name="기존 강사")
        Teacher.objects.create(tenant=self.other, name="다른 학원")
        group = ClassGroup.objects.create(tenant=self.tenant, name="반", kind="union", track="SKY")
        for title, name, teacher in [
            ("연결 강의", "기존 강사", reference), ("제목 복사", "제목 복사", None),
            ("미확인", "다른 학원", None), ("구형 강의", "기존 강사", foreign),
        ]:
            course = Lecture.objects.create(tenant=self.tenant, title=title, name=name)
            ClassGroupLecture.objects.create(group=group, lecture=course, period="vacation", reference_teacher=teacher)
        courses = self.operation().data["groups"][0]["courses"]
        self.assertEqual({course["title"]: course["instructor"] for course in courses}, {
            "연결 강의": "현재 강사", "제목 복사": "", "미확인": "", "구형 강의": "기존 강사",
        })

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

    def test_public_reads_do_not_consume_demo_login_quota(self):
        factory = APIRequestFactory()
        cache = LocMemCache(self.id(), {})
        self.addCleanup(cache.clear)

        def login(role):
            request = factory.post("/api/v1/students/demo-session/", {"role": role}, format="json")
            request.tenant = self.tenant
            return DawonDemoSessionView.as_view()(request)

        with patch.object(AnonRateThrottle, "cache", cache), patch.dict(AnonRateThrottle.THROTTLE_RATES, {"anon": "1/minute"}):
            public = APIView().initialize_request(factory.get("/api/v1/core/program/"))
            self.assertTrue(AnonRateThrottle().allow_request(public, None))
            self.assertFalse(AnonRateThrottle().allow_request(public, None))
            self.assertTrue(DawonDemoLearningThrottle().allow_request(public, None))
            self.assertEqual(login("admin").status_code, 200)
            second = login("teacher")
            self.assertEqual(second.status_code, 429)
            self.assertIn("Retry-After", second)
            self.assertFalse(AnonRateThrottle().allow_request(public, None))
            self.assertTrue(DawonDemoLearningThrottle().allow_request(public, None))

    def test_teacher_homework_write_methods_and_resources(self):
        factory = APIRequestFactory()
        token = {**self.token, "dawon_demo_role": "teacher"}
        for method, path in (
            ("post", "/api/v1/homeworks/"), ("patch", "/api/v1/homeworks/2/"),
            ("patch", "/api/v1/homeworks/2/question-grading/"), ("post", "/api/v1/homeworks/2/source-exam/"),
            ("post", "/api/v1/homeworks/2/save-as-template/"),
        ):
            with self.subTest(method=method, path=path):
                request = getattr(factory, method)(path)
                request.tenant = self.tenant
                _validate_dawon_demo_access(request, token)
                for role in ("admin", "student", "guardian"):
                    with self.assertRaises(PermissionDenied):
                        _validate_dawon_demo_access(request, {**token, "dawon_demo_role": role})
                request.tenant = self.other
                with self.assertRaises(PermissionDenied):
                    _validate_dawon_demo_access(request, token)
        for method, path in (
            ("delete", "/api/v1/homeworks/2/"), ("patch", "/api/v1/homeworks/"),
            ("post", "/api/v1/homeworks/2/question-grading/"), ("patch", "/api/v1/homeworks/2/unknown/"),
        ):
            with self.subTest(method=method, path=path), self.assertRaises(PermissionDenied):
                request = getattr(factory, method)(path)
                request.tenant = self.tenant
                _validate_dawon_demo_access(request, token)
