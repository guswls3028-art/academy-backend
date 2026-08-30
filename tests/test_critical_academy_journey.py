"""Release-blocking Academy account-to-result journey.

This is intentionally one compact scenario so the deploy smoke proves that the
contracts real users cross together still agree, instead of only testing each
model in isolation.
"""

from django.contrib.auth import authenticate, get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import OpsAuditLog, Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.lectures.models import Lecture, Session
from apps.domains.messaging.models import AutoSendConfig, ScheduledNotification
from apps.domains.parents.models import Parent
from apps.domains.results.models import ExamAttempt, Result
from apps.domains.student_app.media.views import StudentVideoMeView
from apps.domains.student_app.results.views import MyExamResultView
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission
from apps.domains.video.models import DirectVideoEntitlement


User = get_user_model()


class CriticalAcademyJourneyGateTests(TestCase):
    password = "Critical-pass-1234"

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            code="critical-journey",
            name="Critical Journey Academy",
            is_active=True,
        )
        cls.student_user = User.objects.create_user(
            username=user_internal_username(cls.tenant, "critical-student"),
            password=cls.password,
            tenant=cls.tenant,
            name="핵심학생",
        )
        TenantMembership.ensure_active(
            tenant=cls.tenant,
            user=cls.student_user,
            role="student",
        )
        cls.parent_user = User.objects.create_user(
            username=user_internal_username(cls.tenant, "critical-parent"),
            password=cls.password,
            tenant=cls.tenant,
            name="핵심학부모",
        )
        TenantMembership.ensure_active(
            tenant=cls.tenant,
            user=cls.parent_user,
            role="parent",
        )
        cls.parent = Parent.objects.create(
            tenant=cls.tenant,
            user=cls.parent_user,
            name="핵심학부모",
            phone="01090000001",
        )
        cls.student = Student.objects.create(
            tenant=cls.tenant,
            user=cls.student_user,
            parent=cls.parent,
            name="핵심학생",
            ps_number="CRITICAL-001",
            omr_code="90000001",
            parent_phone="01090000001",
            school_type="HIGH",
        )
        cls.lecture = Lecture.objects.create(
            tenant=cls.tenant,
            title="핵심 수학",
            name="핵심 수학",
            subject="MATH",
        )
        cls.session = Session.objects.create(
            lecture=cls.lecture,
            order=1,
            title="1회",
        )
        cls.enrollment = Enrollment.objects.create(
            tenant=cls.tenant,
            student=cls.student,
            lecture=cls.lecture,
            status="ACTIVE",
        )
        cls.attendance = Attendance.objects.create(
            tenant=cls.tenant,
            enrollment=cls.enrollment,
            session=cls.session,
            status="PRESENT",
        )
        cls.exam = Exam.objects.create(
            tenant=cls.tenant,
            title="핵심 진단평가",
            exam_type=Exam.ExamType.REGULAR,
            status=Exam.Status.OPEN,
            max_score=100,
            pass_score=60,
            student_results_published=True,
        )
        cls.exam.sessions.add(cls.session)
        ExamEnrollment.objects.create(exam=cls.exam, enrollment=cls.enrollment)
        cls.submission = Submission.objects.create(
            tenant=cls.tenant,
            user=cls.student_user,
            enrollment=cls.enrollment,
            target_type=Submission.TargetType.EXAM,
            target_id=cls.exam.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.DONE,
        )
        cls.attempt = ExamAttempt.objects.create(
            exam=cls.exam,
            enrollment=cls.enrollment,
            submission_id=cls.submission.id,
            attempt_index=1,
            is_representative=True,
            status="done",
        )
        cls.result = Result.objects.create(
            target_type="exam",
            target_id=cls.exam.id,
            enrollment=cls.enrollment,
            attempt=cls.attempt,
            total_score=88,
            max_score=100,
            objective_score=88,
            submitted_at=timezone.now(),
        )

    def setUp(self):
        self.factory = APIRequestFactory()

    def _get(self, path: str, *, user, selected_student: Student | None = None):
        headers = {}
        if selected_student is not None:
            headers["HTTP_X_STUDENT_ID"] = str(selected_student.id)
        request = self.factory.get(path, **headers)
        request.tenant = self.tenant
        force_authenticate(request, user=user)
        return request

    def test_account_enrollment_attendance_result_and_read_safety(self):
        self.assertEqual(
            authenticate(username=self.student_user.username, password=self.password),
            self.student_user,
        )
        self.assertEqual(
            authenticate(username=self.parent_user.username, password=self.password),
            self.parent_user,
        )
        self.assertEqual(self.enrollment.status, "ACTIVE")
        self.assertEqual(self.attendance.status, "PRESENT")

        tracked_models = (
            Lecture,
            Session,
            Enrollment,
            Attendance,
            Result,
            ScheduledNotification,
            AutoSendConfig,
            OpsAuditLog,
            DirectVideoEntitlement,
        )
        before = {model: model.objects.count() for model in tracked_models}

        video_response = StudentVideoMeView.as_view()(
            self._get("/api/v1/student/video/me/", user=self.student_user)
        )
        student_result = MyExamResultView.as_view()(
            self._get(
                f"/api/v1/student/results/me/exams/{self.exam.id}/",
                user=self.student_user,
            ),
            exam_id=self.exam.id,
        )
        parent_result = MyExamResultView.as_view()(
            self._get(
                f"/api/v1/student/results/me/exams/{self.exam.id}/",
                user=self.parent_user,
                selected_student=self.student,
            ),
            exam_id=self.exam.id,
        )

        self.assertEqual(video_response.status_code, 200, video_response.data)
        self.assertIsNone(video_response.data["public"])
        self.assertEqual(student_result.status_code, 200, student_result.data)
        self.assertEqual(parent_result.status_code, 200, parent_result.data)
        self.assertEqual(float(student_result.data["total_score"]), 88)
        self.assertEqual(float(parent_result.data["total_score"]), 88)
        self.assertEqual(
            {model: model.objects.count() for model in tracked_models},
            before,
        )
