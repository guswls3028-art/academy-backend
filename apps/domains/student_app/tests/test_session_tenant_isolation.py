from datetime import timedelta
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant, User
from apps.core.models.user import user_internal_username
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.lectures.models import Lecture, Session
from apps.domains.student_app.permissions import IsStudentOrParent, get_request_student
from apps.domains.student_app.sessions.views import (
    StudentAttendanceSummaryView,
    StudentSessionClearPastView,
    StudentSessionDetailView,
    StudentSessionHideView,
    StudentSessionListView,
)
from apps.domains.students.models import Student


def _create_tenant(code):
    return Tenant.objects.create(code=code, name=code)


def _create_user(tenant, username):
    return User.objects.create_user(
        username=user_internal_username(tenant, username),
        password="testpass123",
        tenant=tenant,
        name=username,
    )


def _create_student(tenant, user, name):
    return Student.objects.create(
        tenant=tenant,
        user=user,
        name=name,
        ps_number=f"PS-{tenant.code}-{name}",
        omr_code="12345678",
        parent_phone="01012345678",
        school_type="HIGH",
    )


def _create_lecture(tenant, title):
    return Lecture.objects.create(
        tenant=tenant,
        title=title,
        name=title,
        subject="math",
    )


def _create_session(lecture, order=1, title="Session", session_date=None):
    return Session.objects.create(
        lecture=lecture,
        order=order,
        title=title,
        date=session_date or timezone.localdate(),
    )


def _request(user, tenant, data=None):
    return SimpleNamespace(
        user=user,
        tenant=tenant,
        data=data or {},
        META={},
    )


class StudentSessionTenantIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant_a = _create_tenant("student-app-a")
        cls.tenant_b = _create_tenant("student-app-b")
        cls.user_a = _create_user(cls.tenant_a, "student-a")
        cls.student_a = _create_student(cls.tenant_a, cls.user_a, "StudentA")
        cls.lecture_a = _create_lecture(cls.tenant_a, "Tenant A Lecture")
        cls.lecture_b = _create_lecture(cls.tenant_b, "Tenant B Lecture")

    def _enroll_student_a(self):
        return Enrollment.objects.create(
            tenant=self.tenant_a,
            student=self.student_a,
            lecture=self.lecture_a,
            status="ACTIVE",
        )

    def test_student_permission_requires_matching_request_tenant(self):
        matching_request = _request(self.user_a, self.tenant_a)
        cross_tenant_request = _request(self.user_a, self.tenant_b)

        permission = IsStudentOrParent()

        self.assertTrue(permission.has_permission(matching_request, None))
        self.assertEqual(get_request_student(matching_request), self.student_a)
        self.assertFalse(permission.has_permission(cross_tenant_request, None))
        self.assertIsNone(get_request_student(cross_tenant_request))

    def test_deleted_student_is_not_active_request_student(self):
        self.student_a.deleted_at = timezone.now()
        self.student_a.save(update_fields=["deleted_at", "updated_at"])

        request = _request(self.user_a, self.tenant_a)
        permission = IsStudentOrParent()

        self.assertFalse(permission.has_permission(request, None))
        self.assertIsNone(get_request_student(request))

    def test_session_list_ignores_cross_tenant_session_enrollment_rows(self):
        enrollment = self._enroll_student_a()
        own_session = _create_session(self.lecture_a, title="Own session")
        foreign_session = _create_session(self.lecture_b, title="Foreign session")
        SessionEnrollment.objects.create(
            tenant=self.tenant_a,
            enrollment=enrollment,
            session=own_session,
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant_a,
            enrollment=enrollment,
            session=foreign_session,
        )

        response = StudentSessionListView().get(_request(self.user_a, self.tenant_a))

        session_ids = {item["id"] for item in response.data}
        self.assertIn(own_session.id, session_ids)
        self.assertNotIn(foreign_session.id, session_ids)

    def test_session_detail_and_hide_reject_cross_tenant_session_enrollment_rows(self):
        enrollment = self._enroll_student_a()
        foreign_session = _create_session(self.lecture_b, title="Foreign detail")
        SessionEnrollment.objects.create(
            tenant=self.tenant_a,
            enrollment=enrollment,
            session=foreign_session,
        )

        detail_response = StudentSessionDetailView().get(
            _request(self.user_a, self.tenant_a),
            foreign_session.id,
        )
        hide_response = StudentSessionHideView().post(
            _request(self.user_a, self.tenant_a, {"id": foreign_session.id})
        )

        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(hide_response.status_code, 404)
        self.student_a.refresh_from_db()
        self.assertEqual(self.student_a.schedule_hidden_ids, [])

    def test_session_hide_rejects_inactive_own_enrollment(self):
        enrollment = self._enroll_student_a()
        session = _create_session(self.lecture_a, title="Inactive own session")
        SessionEnrollment.objects.create(
            tenant=self.tenant_a,
            enrollment=enrollment,
            session=session,
        )
        enrollment.status = "INACTIVE"
        enrollment.save(update_fields=["status", "updated_at"])

        response = StudentSessionHideView().post(
            _request(self.user_a, self.tenant_a, {"id": session.id})
        )

        self.assertEqual(response.status_code, 404)
        self.student_a.refresh_from_db()
        self.assertEqual(self.student_a.schedule_hidden_ids, [])

    def test_attendance_summary_ignores_inactive_own_enrollment(self):
        Attendance = django_apps.get_model("attendance", "Attendance")
        active_enrollment = self._enroll_student_a()
        inactive_lecture = _create_lecture(self.tenant_a, "Inactive Attendance Lecture")
        inactive_enrollment = Enrollment.objects.create(
            tenant=self.tenant_a,
            student=self.student_a,
            lecture=inactive_lecture,
            status="INACTIVE",
        )
        active_session = _create_session(self.lecture_a, order=1, title="Active attendance")
        inactive_session = _create_session(inactive_lecture, order=1, title="Inactive attendance")
        Attendance.objects.create(
            tenant=self.tenant_a,
            enrollment=active_enrollment,
            session=active_session,
            status="PRESENT",
        )
        Attendance.objects.create(
            tenant=self.tenant_a,
            enrollment=inactive_enrollment,
            session=inactive_session,
            status="ABSENT",
        )

        response = StudentAttendanceSummaryView().get(_request(self.user_a, self.tenant_a))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["total"], 1)
        self.assertEqual(response.data["summary"]["present"], 1)
        self.assertEqual(response.data["summary"]["absent"], 0)
        recent_session_ids = {row["session_id"] for row in response.data["recent"]}
        self.assertIn(active_session.id, recent_session_ids)
        self.assertNotIn(inactive_session.id, recent_session_ids)

    def test_clear_past_does_not_keep_cross_tenant_future_hidden_session_ids(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        own_session = _create_session(
            self.lecture_a,
            title="Own future",
            session_date=tomorrow,
        )
        foreign_session = _create_session(
            self.lecture_b,
            title="Foreign future",
            session_date=tomorrow,
        )
        self.student_a.schedule_hidden_ids = [own_session.id, foreign_session.id]
        self.student_a.save(update_fields=["schedule_hidden_ids", "updated_at"])

        response = StudentSessionClearPastView().post(_request(self.user_a, self.tenant_a))

        self.assertEqual(response.status_code, 200)
        self.student_a.refresh_from_db()
        self.assertEqual(self.student_a.schedule_hidden_ids, [own_session.id])
