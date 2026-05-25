from __future__ import annotations

from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.clinic.models import Session as ClinicSession, SessionParticipant
from apps.domains.messaging.models import AutoSendConfig
from apps.domains.students.models import Student
from apps.support.clinic.session_dependencies import (
    send_clinic_reminder_for_students,
    send_due_clinic_reminders,
)


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
        self.assertEqual(kwargs["send_to"], "student")
        self.assertEqual(kwargs["context"]["장소"], "3층 세미나실")
        self.assertEqual(kwargs["context"]["날짜"], "2026-05-15")
        self.assertEqual(kwargs["context"]["시간"], "18:30")

    @patch(
        "apps.support.clinic.session_dependencies.send_clinic_reminder_for_students",
        return_value={"status": "ok", "attempted": 1, "sent": 1, "skipped": 0},
    )
    def test_send_due_clinic_reminders_picks_due_sessions(self, mock_send):
        AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_reminder",
            enabled=True,
            minutes_before=30,
        )
        now = timezone.make_aware(datetime(2026, 5, 15, 18, 0), timezone.get_current_timezone())
        due_session = ClinicSession.objects.create(
            tenant=self.tenant,
            title="정시 알림",
            date=now.date(),
            start_time=(now + timedelta(minutes=30)).time(),
            duration_minutes=60,
            location="2층",
            max_participants=12,
        )
        later_session = ClinicSession.objects.create(
            tenant=self.tenant,
            title="아직 아님",
            date=now.date(),
            start_time=(now + timedelta(minutes=45)).time(),
            duration_minutes=60,
            location="4층",
            max_participants=12,
        )
        student = self._student("003", "정시학생")
        SessionParticipant.objects.create(
            tenant=self.tenant,
            session=due_session,
            student=student,
            status=SessionParticipant.Status.BOOKED,
        )
        SessionParticipant.objects.create(
            tenant=self.tenant,
            session=later_session,
            student=student,
            status=SessionParticipant.Status.BOOKED,
        )

        result = send_due_clinic_reminders(now=now, window_minutes=5)

        self.assertEqual(result["sessions_due"], 1)
        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["sent"], 1)
        mock_send.assert_called_once_with(session_id=due_session.id)

    @patch("apps.support.clinic.session_dependencies.send_clinic_reminder_for_students")
    def test_send_due_clinic_reminders_ignores_disabled_configs(self, mock_send):
        AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_reminder",
            enabled=False,
            minutes_before=30,
        )
        now = timezone.make_aware(datetime(2026, 5, 15, 18, 0), timezone.get_current_timezone())
        result = send_due_clinic_reminders(now=now, tenant_id=self.tenant.id)

        self.assertEqual(result["configs"], 0)
        self.assertEqual(result["sessions_due"], 0)
        mock_send.assert_not_called()
