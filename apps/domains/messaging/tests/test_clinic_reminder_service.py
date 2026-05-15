from __future__ import annotations

from datetime import date, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.clinic.models import Session as ClinicSession, SessionParticipant
from apps.domains.messaging.services.notification_service import send_clinic_reminder_for_students
from apps.domains.students.models import Student


User = get_user_model()


class ClinicReminderServiceTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Clinic Academy",
            code="clinic-reminder",
            is_active=True,
        )
        self.session = ClinicSession.objects.create(
            tenant=self.tenant,
            title="금요 보강",
            date=date(2026, 5, 15),
            start_time=time(18, 30),
            duration_minutes=60,
            location="3층 세미나실",
            max_participants=12,
        )

    def _student(self, suffix: str, name: str) -> Student:
        user = User.objects.create_user(
            tenant=self.tenant,
            username=f"clinic_student_{suffix}",
            password="pass1234",
        )
        return Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number=f"PS{suffix}",
            omr_code=f"1234{suffix}".zfill(8)[-8:],
            name=name,
            phone="01011112222",
            parent_phone="01033334444",
        )

    @patch("apps.domains.messaging.services.notification_service.send_event_notification", return_value=True)
    def test_sends_clinic_reminder_to_booked_participants_only(self, mock_send):
        booked = self._student("001", "예약학생")
        cancelled = self._student("002", "취소학생")
        SessionParticipant.objects.create(
            tenant=self.tenant,
            session=self.session,
            student=booked,
            status=SessionParticipant.Status.BOOKED,
        )
        SessionParticipant.objects.create(
            tenant=self.tenant,
            session=self.session,
            student=cancelled,
            status=SessionParticipant.Status.CANCELLED,
        )

        result = send_clinic_reminder_for_students(session_id=self.session.id)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["sent"], 1)
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["tenant"], self.tenant)
        self.assertEqual(kwargs["trigger"], "clinic_reminder")
        self.assertEqual(kwargs["student"], booked)
        self.assertEqual(kwargs["send_to"], "parent")
        self.assertEqual(kwargs["context"]["장소"], "3층 세미나실")
        self.assertEqual(kwargs["context"]["날짜"], "2026-05-15")
        self.assertEqual(kwargs["context"]["시간"], "18:30")
