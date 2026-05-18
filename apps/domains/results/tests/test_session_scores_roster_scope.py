from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.views.session_scores_view import SessionScoresView
from apps.domains.students.models import Student


User = get_user_model()


class SessionScoresRosterScopeTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Tenant", code="scorecope", is_active=True)
        self.admin = User.objects.create_user(
            username="score_scope_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture",
            name="Lecture",
            subject="SCIENCE",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1주차")
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="주간 테스트",
            pass_score=60,
            max_score=100,
        )
        self.exam.sessions.add(self.session)
        self.homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="주간 과제",
        )

        self.active_enrollment = self._create_enrollment("ACTIVE001", "현재 학생")
        self.stale_enrollment = self._create_enrollment("STALE001", "출결 제외 학생")

        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.active_enrollment,
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.stale_enrollment,
        )
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.active_enrollment,
            status="PRESENT",
        )

        for enrollment in (self.active_enrollment, self.stale_enrollment):
            ExamEnrollment.objects.create(exam=self.exam, enrollment=enrollment)
            HomeworkAssignment.objects.create(
                tenant=self.tenant,
                homework=self.homework,
                session=self.session,
                enrollment=enrollment,
            )

    def _create_enrollment(self, ps_number: str, name: str) -> Enrollment:
        user = User.objects.create_user(
            username=f"score_scope_{ps_number}",
            password="test1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number=ps_number,
            omr_code=ps_number[-8:],
            name=name,
            parent_phone="01000000000",
        )
        return Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=self.lecture,
            status="ACTIVE",
        )

    def test_session_scores_excludes_assignment_without_attendance_row(self):
        request = self.factory.get(f"/api/v1/results/admin/sessions/{self.session.id}/scores/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = SessionScoresView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data["rows"]
        self.assertEqual([row["enrollment_id"] for row in rows], [self.active_enrollment.id])
        self.assertEqual(rows[0]["student_name"], "현재 학생")
        self.assertEqual(len(rows[0]["exams"]), 1)
        self.assertEqual(len(rows[0]["homeworks"]), 1)
