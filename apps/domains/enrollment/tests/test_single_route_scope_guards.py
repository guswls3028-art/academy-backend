from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student


User = get_user_model()


class EnrollmentAttendanceSingleRouteScopeGuardTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Scope A", code="scope-a", is_active=True)
        self.other_tenant = Tenant.objects.create(name="Scope B", code="scope-b", is_active=True)
        self.admin = User.objects.create_user(
            username="scope-a-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture A",
            name="Lecture A",
            subject="MATH",
        )
        self.other_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture A2",
            name="Lecture A2",
            subject="MATH",
        )
        self.foreign_lecture = Lecture.objects.create(
            tenant=self.other_tenant,
            title="Lecture B",
            name="Lecture B",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="S1")
        self.other_session = Session.objects.create(
            lecture=self.other_lecture,
            order=1,
            title="S1",
        )
        self.foreign_session = Session.objects.create(
            lecture=self.foreign_lecture,
            order=1,
            title="S1",
        )

        self.student_user = User.objects.create_user(
            username="scope-a-student",
            password="pw1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            name="Student A",
            ps_number="SG-A-001",
            omr_code="SGA00001",
            parent_phone="01012345678",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            student=self.student,
            status="ACTIVE",
        )
        self.attendance = Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
            status="PRESENT",
        )
        self.session_enrollment = SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        self.client.force_authenticate(user=self.admin)

    def _headers(self):
        return {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant.code}

    def test_default_create_routes_are_blocked(self):
        attendance_resp = self.client.post(
            "/api/v1/lectures/attendance/",
            {
                "session": self.session.id,
                "enrollment_id": self.enrollment.id,
                "status": "PRESENT",
            },
            format="json",
            **self._headers(),
        )
        enrollment_resp = self.client.post(
            "/api/v1/enrollments/",
            {"lecture": self.lecture.id, "status": "ACTIVE"},
            format="json",
            **self._headers(),
        )
        session_enrollment_resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/",
            {"session": self.session.id, "enrollment": self.enrollment.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(attendance_resp.status_code, 405, attendance_resp.data)
        self.assertEqual(enrollment_resp.status_code, 405, enrollment_resp.data)
        self.assertEqual(session_enrollment_resp.status_code, 405, session_enrollment_resp.data)

    def test_attendance_patch_cannot_rebind_session_or_enrollment(self):
        resp = self.client.patch(
            f"/api/v1/lectures/attendance/{self.attendance.id}/",
            {"session": self.other_session.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.session_id, self.session.id)

    def test_attendance_patch_rejects_foreign_session(self):
        resp = self.client.patch(
            f"/api/v1/lectures/attendance/{self.attendance.id}/",
            {"session": self.foreign_session.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.session_id, self.session.id)

    def test_enrollment_patch_cannot_rebind_lecture(self):
        resp = self.client.patch(
            f"/api/v1/enrollments/{self.enrollment.id}/",
            {"lecture": self.other_lecture.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.lecture_id, self.lecture.id)

    def test_session_enrollment_patch_cannot_cross_lecture(self):
        resp = self.client.patch(
            f"/api/v1/enrollments/session-enrollments/{self.session_enrollment.id}/",
            {"session": self.other_session.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.session_enrollment.refresh_from_db()
        self.assertEqual(self.session_enrollment.session_id, self.session.id)
