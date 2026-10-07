from datetime import datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import TenantMembership
from apps.domains.clinic.test_support import create_clinic_session_fixture, create_clinic_participant_fixture
from apps.domains.enrollment.test_support import create_enrollment_fixture, create_session_enrollment_fixture
from apps.domains.student_app.dashboard.views import StudentDashboardView
from apps.domains.student_app.tests.test_session_tenant_isolation import (
    _create_lecture, _create_session, _create_student, _create_tenant, _create_user,
)
from apps.support.student_app.dashboard_dependencies import (
    today_lecture_sessions_for_dashboard, upcoming_clinic_count_for_dashboard,
)
from apps.support.student_app.session_dependencies import get_student_lecture_sessions


class DashboardLearningTodoTests(TestCase):
    def setUp(self):
        self.tenant = _create_tenant("todo-dashboard")
        self.user = _create_user(self.tenant, "student")
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        self.student = _create_student(self.tenant, self.user, "Student")
        self.now = timezone.make_aware(datetime(2026, 10, 8, 12))
        self.booking_index = 0

    def _booking(self, *, date=None, start=time(12), duration=60, status="booked", **kwargs):
        self.booking_index += 1
        session = create_clinic_session_fixture(
            tenant=self.tenant, date=date or self.now.date(), start_time=start,
            duration_minutes=duration, location=f"Room-{self.booking_index}", max_participants=10,
        )
        return create_clinic_participant_fixture(
            tenant=self.tenant, student=self.student, session=session, status=status, **kwargs,
        )

    def _count(self, now=None):
        return upcoming_clinic_count_for_dashboard(tenant=self.tenant, student=self.student, now=now or self.now)

    def test_clinic_badges_count_only_current_or_seven_day_bookings(self):
        self._booking()  # Starts now, still actionable.
        self._booking(date=self.now.date() + timedelta(days=7), status="pending")
        self._booking(start=time(11))  # Ends exactly now.
        self._booking(date=self.now.date() + timedelta(days=7), start=time(12, 1))
        for status in ("cancelled", "rejected", "attended", "no_show"):
            self._booking(status=status)
        self.assertEqual(self._count(), 2)

    def test_clinic_badges_use_actual_booking_end_and_overnight_window(self):
        self._booking(start=time(10), duration=240, booking_start_time=time(10), booking_end_time=time(11))
        self._booking(start=time(10), duration=240, booking_start_time=time(12), booking_end_time=time(13))
        self.assertEqual(self._count(), 1)
        yesterday = self.now.date() - timedelta(days=1)
        self._booking(date=yesterday, start=time(23), duration=180, booking_start_time=time(0), booking_end_time=time(1))
        overnight_now = self.now.replace(hour=0, minute=30)
        self.assertEqual(self._count(overnight_now), 3)
        self.assertEqual(self._count(self.now.replace(hour=1, minute=0)), 2)

    def test_clinic_badges_fail_closed_for_foreign_session_or_student(self):
        other_tenant = _create_tenant("todo-other")
        foreign = self._booking()
        foreign.session.tenant = other_tenant
        foreign.session.save(update_fields=["tenant", "updated_at"])
        other_student = _create_student(self.tenant, _create_user(self.tenant, "other"), "Other")
        booking = self._booking()
        booking.student = other_student
        booking.save(update_fields=["student", "updated_at"])
        self.assertEqual(self._count(), 0)

    def test_dashboard_returns_real_booking_count(self):
        self._booking(date=timezone.localdate() + timedelta(days=1))
        request = APIRequestFactory().get("/student/dashboard/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        response = StudentDashboardView.as_view()(request)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["badges"]["clinic_upcoming"])
        self.assertEqual(response.data["badges"]["clinic_upcoming_count"], 1)

    def test_today_lesson_excludes_ended_and_mismatched_course_but_keeps_end_date(self):
        lecture = _create_lecture(self.tenant, "Vacation")
        session = _create_session(lecture, session_date=self.now.date())
        enrollment = create_enrollment_fixture(tenant=self.tenant, student=self.student, lecture=lecture, status="ACTIVE")
        roster = create_session_enrollment_fixture(tenant=self.tenant, enrollment=enrollment, session=session)
        for offset, expected in ((-1, []), (0, [session.id])):
            lecture.end_date = self.now.date() + timedelta(days=offset)
            lecture.save(update_fields=["end_date", "updated_at"])
            rows = today_lecture_sessions_for_dashboard(tenant=self.tenant, student=self.student, today=self.now.date())
            self.assertEqual(list(rows.values_list("id", flat=True)), expected)
        other_lecture = _create_lecture(self.tenant, "Other course")
        enrollment.lecture = other_lecture
        enrollment.save(update_fields=["lecture", "updated_at"])
        roster.refresh_from_db()
        self.assertFalse(today_lecture_sessions_for_dashboard(tenant=self.tenant, student=self.student, today=self.now.date()).exists())

    def test_upcoming_schedule_respects_course_dates_while_retaining_past_lessons(self):
        today = timezone.localdate()
        lecture = _create_lecture(self.tenant, "Vacation schedule")
        lecture.start_date = today - timedelta(days=7)
        lecture.end_date = today - timedelta(days=1)
        lecture.save(update_fields=["start_date", "end_date", "updated_at"])
        past = _create_session(lecture, order=1, session_date=today - timedelta(days=2))
        invalid_future = _create_session(lecture, order=2, session_date=today + timedelta(days=1))
        rows = get_student_lecture_sessions(
            session_ids=[past.id, invalid_future.id], tenant=self.tenant,
            hidden_before=None, hidden_session_ids=set(),
        )
        self.assertEqual([row.id for row in rows], [past.id])
        lecture.end_date = invalid_future.date
        lecture.save(update_fields=["end_date", "updated_at"])
        rows = get_student_lecture_sessions(
            session_ids=[past.id, invalid_future.id], tenant=self.tenant,
            hidden_before=None, hidden_session_ids=set(),
        )
        self.assertEqual([row.id for row in rows], [past.id, invalid_future.id])
