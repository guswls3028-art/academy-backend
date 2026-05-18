from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.attendance.views import AttendanceViewSet
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student


User = get_user_model()


class AttendanceDestroyRosterCleanupTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Tenant", code="attdel", is_active=True)
        self.admin = User.objects.create_user(
            username="attdel_admin",
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
        self.other_session = Session.objects.create(lecture=self.lecture, order=2, title="2주차")

        student_user = User.objects.create_user(
            username="attdel_student",
            password="test1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            ps_number="ATTDEL001",
            omr_code="12345678",
            name="이준우",
            parent_phone="01000000000",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.other_session,
            enrollment=self.enrollment,
        )
        self.attendance = Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
            status="PRESENT",
        )
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.other_session,
            enrollment=self.enrollment,
            status="PRESENT",
        )

        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="Current Exam",
            pass_score=60,
            max_score=100,
        )
        self.exam.sessions.add(self.session)
        self.other_exam = Exam.objects.create(
            tenant=self.tenant,
            title="Other Exam",
            pass_score=60,
            max_score=100,
        )
        self.other_exam.sessions.add(self.other_session)
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        ExamEnrollment.objects.create(exam=self.other_exam, enrollment=self.enrollment)

        self.homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Current Homework",
        )
        self.other_homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.other_session,
            title="Other Homework",
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=self.homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=self.other_homework,
            session=self.other_session,
            enrollment=self.enrollment,
        )

    def test_destroy_removes_current_session_roster_and_score_targets_only(self):
        request = self.factory.delete(f"/api/v1/lectures/attendance/{self.attendance.id}/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        view = AttendanceViewSet.as_view({"delete": "destroy"})

        response = view(request, pk=self.attendance.id)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Attendance.objects.filter(id=self.attendance.id).exists())
        self.assertFalse(
            SessionEnrollment.objects.filter(
                tenant=self.tenant,
                session=self.session,
                enrollment=self.enrollment,
            ).exists()
        )
        self.assertFalse(
            ExamEnrollment.objects.filter(
                exam=self.exam,
                enrollment=self.enrollment,
            ).exists()
        )
        self.assertFalse(
            HomeworkAssignment.objects.filter(
                tenant=self.tenant,
                homework=self.homework,
                enrollment=self.enrollment,
            ).exists()
        )

        self.assertTrue(
            SessionEnrollment.objects.filter(
                tenant=self.tenant,
                session=self.other_session,
                enrollment=self.enrollment,
            ).exists()
        )
        self.assertTrue(
            ExamEnrollment.objects.filter(
                exam=self.other_exam,
                enrollment=self.enrollment,
            ).exists()
        )
        self.assertTrue(
            HomeworkAssignment.objects.filter(
                tenant=self.tenant,
                homework=self.other_homework,
                enrollment=self.enrollment,
            ).exists()
        )
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "ACTIVE")
