import datetime
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.domains.clinic.contracts import is_clinic_booking_reminder_active
from apps.domains.clinic.models import SessionParticipant
from apps.domains.clinic.services.lifecycle import (
    _participant_booking_end_at,
    booking_availability_for_session,
)
from apps.domains.clinic.services.passcard_state import passcard_confirmed_student_ids
from apps.domains.clinic.tests import ClinicAPITestMixin


@override_settings(
    CLINIC_MIDNIGHT_TIME_RANGE_WRITES_ENABLED=True,
    CLINIC_OVERNIGHT_TIME_RANGE_WRITES_ENABLED=True,
)
class ClinicOvernightTimeRangeTests(APITestCase, ClinicAPITestMixin):
    def setUp(self):
        self.data = self.setup_api_tenant("clinic_overnight", student_count=2)
        self.tenant = self.data["tenant"]
        self.session = self.data["clinic_session"]
        self.session.date = timezone.localdate() + datetime.timedelta(days=1)
        self.session.start_time = datetime.time(23)
        self.session.duration_minutes = 180
        self.session.booking_mode = "time_range"
        self.session.booking_interval_minutes = 30
        self.session.booking_max_stay_minutes = 180
        self.session.max_participants = 1
        self.session.save()

    def _headers(self):
        return super()._headers(self.tenant)

    def _book(self, *, student_index=0, start="23:30", end="01:00", staff=False):
        student = self.data["students"][student_index]
        self.client.force_authenticate(user=self.data["admin_user"] if staff else student.user)
        payload = {
            "session": self.session.id, "student": student.id,
            "booking_start_time": start, "booking_end_time": end,
        } if staff else {
            "session_ids": [self.session.id],
            "booking_start_time": start, "booking_end_time": end,
        }
        return self.client.post(
            "/api/v1/clinic/participants/" if staff else "/api/v1/clinic/participants/bulk-create/",
            payload, format="json", **self._headers(),
        )

    def test_teacher_creates_overnight_and_student_books_crossing_midnight(self):
        self.client.force_authenticate(user=self.data["admin_user"])
        created = self.client.post("/api/v1/clinic/sessions/", {
            "date": self.session.date, "start_time": "23:00", "duration_minutes": 180,
            "location": "overnight-create", "max_participants": 1,
            "booking_mode": "time_range", "booking_interval_minutes": 30,
        }, format="json", **self._headers())
        self.assertEqual(created.status_code, 201, created.data)
        booked = self._book()
        self.assertEqual(booked.status_code, 201, booked.data)
        participant = SessionParticipant.objects.get(session=self.session, student=self.data["students"][0])
        self.assertEqual(participant.booking_end_time, datetime.time(1))
        slots = booking_availability_for_session(tenant=self.tenant, session=self.session)
        by_start = {slot["start_time"]: slot for slot in slots["slots"]}
        self.assertEqual(by_start["23:00"]["remaining_capacity"], 1)
        self.assertEqual(by_start["00:30"]["remaining_capacity"], 0)
        self.assertEqual(by_start["01:00"]["remaining_capacity"], 1)
        self.assertEqual(by_start["00:30"]["start_date"], str(self.session.date + datetime.timedelta(days=1)))
        full = self._book(student_index=1, start="00:30", end="01:30")
        self.assertEqual(full.status_code, 409, full.data)

    def test_staff_manually_adds_after_midnight_range_and_reminder_date_is_exact(self):
        response = self._book(start="00:30", end="01:30", staff=True)
        self.assertEqual(response.status_code, 201, response.data)
        participant = SessionParticipant.objects.get(session=self.session, student=self.data["students"][0])
        next_day = self.session.date + datetime.timedelta(days=1)
        end = _participant_booking_end_at(participant)
        self.assertEqual(timezone.localtime(end).date(), next_day)
        self.assertEqual(timezone.localtime(end).time(), datetime.time(1, 30))
        now = timezone.make_aware(datetime.datetime.combine(self.session.date, datetime.time(22)))
        for origin_date, expected in ((next_day, True), (self.session.date, False), (next_day + datetime.timedelta(days=1), False)):
            with self.subTest(origin_date=origin_date):
                self.assertEqual(is_clinic_booking_reminder_active(
                    tenant_id=self.tenant.id,
                    origin_id=f"clinic_booking:{participant.id}:{self.session.id}:{origin_date:%Y%m%d}:0030",
                    now=now,
                ), expected)

    def test_student_can_book_yesterday_session_that_is_still_running(self):
        after_midnight = timezone.make_aware(datetime.datetime.combine(
            self.session.date + datetime.timedelta(days=1), datetime.time(0, 10),
        ))
        with patch("django.utils.timezone.now", return_value=after_midnight):
            response = self._book(start="00:30", end="01:30")
        self.assertEqual(response.status_code, 201, response.data)

    def test_active_overnight_passcard_keeps_actual_date_then_expires(self):
        response = self._book(start="00:30", end="01:30", staff=True)
        self.assertEqual(response.status_code, 201, response.data)
        student = self.data["students"][0]
        self.client.force_authenticate(user=student.user)
        next_day = self.session.date + datetime.timedelta(days=1)
        for hour, expected in ((1, True), (2, False)):
            with self.subTest(hour=hour):
                now = timezone.make_aware(datetime.datetime.combine(next_day, datetime.time(hour)))
                with patch("django.utils.timezone.now", return_value=now):
                    idcard = self.client.get("/api/v1/clinic/idcard/", **self._headers())
                    self.assertEqual(idcard.status_code, 200, idcard.data)
                    self.assertEqual(bool(idcard.data["valid_bookings"]), expected)
                    if expected:
                        self.assertEqual(idcard.data["valid_bookings"][0]["date"], str(next_day))
                        self.assertEqual(idcard.data["valid_bookings"][0]["start_time"], "00:30:00")
                    confirmed = passcard_confirmed_student_ids(tenant=self.tenant, student_ids=[student.id])
                    self.assertEqual(student.id in confirmed, expected)

    def test_adjacent_schedule_dates_cannot_hide_overlapping_booking(self):
        first = self._book(staff=True)
        self.assertEqual(first.status_code, 201, first.data)
        original = self.session
        self.session = original.__class__.objects.create(
            tenant=self.tenant, date=original.date + datetime.timedelta(days=1),
            start_time=datetime.time(0, 30), duration_minutes=120, max_participants=2,
            booking_mode="time_range", booking_interval_minutes=30, booking_max_stay_minutes=120,
        )
        conflicting = self._book(start="00:30", end="01:30", staff=True)
        self.assertEqual(conflicting.status_code, 409, conflicting.data)
        adjacent = self._book(start="01:00", end="01:30", staff=True)
        self.assertEqual(adjacent.status_code, 201, adjacent.data)

    def test_session_duration_of_a_full_day_is_rejected(self):
        self.client.force_authenticate(user=self.data["admin_user"])
        for duration in (0, 1440, 1470):
            with self.subTest(duration=duration):
                response = self.client.patch(
                    f"/api/v1/clinic/sessions/{self.session.id}/", {"duration_minutes": duration},
                    format="json", **self._headers(),
                )
                self.assertEqual(response.status_code, 400, response.data)

    def test_outside_equal_and_too_long_ranges_are_rejected_without_rows(self):
        for start, end in (("22:30", "00:30"), ("01:30", "02:30"), ("00:30", "00:30")):
            with self.subTest(start=start, end=end):
                response = self._book(start=start, end=end)
                self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(SessionParticipant.objects.filter(session=self.session).exists())

    @override_settings(CLINIC_OVERNIGHT_TIME_RANGE_WRITES_ENABLED=False)
    def test_overnight_writes_wait_for_reader_convergence(self):
        response = self._book()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(SessionParticipant.objects.filter(session=self.session).exists())
