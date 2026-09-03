from __future__ import annotations

from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.clinic.models import Session as ClinicSession, SessionParticipant
from apps.domains.messaging.alimtalk_content_builders import get_solapi_template_id
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, ScheduledNotification
from apps.domains.students.models import Student
from apps.support.clinic.session_dependencies import (
    send_clinic_reminder_for_participant,
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
        self.assertEqual(kwargs["context"]["_source_domain"], "clinic")
        self.assertEqual(kwargs["context"]["_source_use_case"], "clinic.reminder")

    @patch("apps.domains.messaging.services.notification_service.send_event_notification", return_value=True)
    def test_staff_manual_reminder_targets_one_booked_participant(self, mock_send):
        first = self._student("011", "재촉학생")
        second = self._student("012", "다른학생")
        participant = SessionParticipant.objects.create(
            tenant=self.tenant,
            session=self.session,
            student=first,
            status=SessionParticipant.Status.BOOKED,
        )
        SessionParticipant.objects.create(
            tenant=self.tenant,
            session=self.session,
            student=second,
            status=SessionParticipant.Status.BOOKED,
        )
        now = timezone.make_aware(
            datetime(2026, 5, 15, 18, 5),
            timezone.get_current_timezone(),
        )

        result = send_clinic_reminder_for_participant(
            tenant_id=self.tenant.id,
            participant_id=participant.id,
            actor_id=77,
            now=now,
        )

        self.assertEqual(result, {"status": "ok", "sent": 1, "skipped": 0})
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["tenant"], self.tenant)
        self.assertEqual(kwargs["trigger"], "clinic_reminder")
        self.assertEqual(kwargs["student"], first)
        self.assertEqual(kwargs["send_to"], "student")
        self.assertEqual(kwargs["context"]["_source_use_case"], "clinic.manual_reminder")
        self.assertEqual(kwargs["context"]["_actor_id"], "77")
        self.assertTrue(
            kwargs["context"]["_domain_object_id"].startswith(
                f"clinic_participant:{participant.id}:manual_reminder:202605151805"
            )
        )

    @patch("apps.domains.messaging.services.notification_service.send_event_notification", return_value=True)
    def test_staff_can_repeat_manual_reminder_within_the_same_minute(self, mock_send):
        student = self._student("015", "반복재촉학생")
        participant = SessionParticipant.objects.create(
            tenant=self.tenant,
            session=self.session,
            student=student,
            status=SessionParticipant.Status.BOOKED,
        )
        now = timezone.make_aware(
            datetime(2026, 5, 15, 18, 5),
            timezone.get_current_timezone(),
        )

        first = send_clinic_reminder_for_participant(
            tenant_id=self.tenant.id,
            participant_id=participant.id,
            actor_id=77,
            now=now,
        )
        second = send_clinic_reminder_for_participant(
            tenant_id=self.tenant.id,
            participant_id=participant.id,
            actor_id=77,
            now=now,
        )

        self.assertEqual(first, {"status": "ok", "sent": 1, "skipped": 0})
        self.assertEqual(second, {"status": "ok", "sent": 1, "skipped": 0})
        self.assertEqual(mock_send.call_count, 2)
        occurrence_keys = [
            call.kwargs["context"]["_domain_object_id"]
            for call in mock_send.call_args_list
        ]
        self.assertEqual(len(set(occurrence_keys)), 2)

    @patch("apps.domains.messaging.services.notification_service.send_event_notification")
    def test_staff_manual_reminder_rejects_non_booked_participant(self, mock_send):
        student = self._student("013", "출석학생")
        participant = SessionParticipant.objects.create(
            tenant=self.tenant,
            session=self.session,
            student=student,
            status=SessionParticipant.Status.ATTENDED,
        )

        result = send_clinic_reminder_for_participant(
            tenant_id=self.tenant.id,
            participant_id=participant.id,
        )

        self.assertEqual(result, {"status": "invalid_status", "sent": 0, "skipped": 1})
        mock_send.assert_not_called()

    @patch("apps.domains.messaging.services.notification_service.send_event_notification")
    def test_staff_manual_reminder_is_tenant_scoped(self, mock_send):
        student = self._student("014", "테넌트학생")
        participant = SessionParticipant.objects.create(
            tenant=self.tenant,
            session=self.session,
            student=student,
            status=SessionParticipant.Status.BOOKED,
        )
        other_tenant = Tenant.objects.create(
            name="Other Academy",
            code="clinic-reminder-other",
            is_active=True,
        )

        result = send_clinic_reminder_for_participant(
            tenant_id=other_tenant.id,
            participant_id=participant.id,
        )

        self.assertEqual(result, {"status": "not_found", "sent": 0, "skipped": 1})
        mock_send.assert_not_called()

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

    @patch("apps.domains.messaging.policy.get_owner_tenant_id")
    @patch("apps.domains.messaging.policy.is_messaging_disabled", return_value=False)
    def test_send_due_clinic_reminders_dispatches_each_session_once_within_window(
        self,
        _mock_disabled,
        mock_owner_tenant_id,
    ):
        mock_owner_tenant_id.return_value = self.tenant.id
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            category=MessageTemplate.Category.CLINIC,
            name="클리닉 시작 알림",
            body="#{학생이름} 학생, #{시간} 클리닉이 곧 시작됩니다.",
        )
        AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_reminder",
            enabled=True,
            minutes_before=30,
            message_mode="alimtalk",
            template=template,
        )
        due_at = timezone.make_aware(
            datetime(2026, 5, 15, 18, 0),
            timezone.get_current_timezone(),
        )
        due_session = ClinicSession.objects.create(
            tenant=self.tenant,
            title="정확히 한 번 알림",
            date=due_at.date(),
            start_time=(due_at + timedelta(minutes=30)).time(),
            duration_minutes=60,
            location="2층",
            max_participants=12,
        )
        student = self._student("004", "한번학생")
        SessionParticipant.objects.create(
            tenant=self.tenant,
            session=due_session,
            student=student,
            status=SessionParticipant.Status.BOOKED,
        )

        first = send_due_clinic_reminders(now=due_at, window_minutes=5)
        second = send_due_clinic_reminders(
            now=due_at + timedelta(minutes=1),
            window_minutes=5,
        )

        self.assertEqual(first["sessions_due"], 1)
        self.assertEqual(second["sessions_due"], 0)
        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(second["deduplicated"], 1)
        outbox = ScheduledNotification.objects.get(
            tenant=self.tenant,
            trigger="clinic_reminder",
        )
        self.assertEqual(outbox.origin_id, f"clinic_session:{due_session.id}:reminder")
        self.assertEqual(outbox.payload["message_mode"], "alimtalk")
        self.assertEqual(
            outbox.payload["template_id"],
            get_solapi_template_id("clinic_reminder"),
        )
        self.assertEqual(outbox.payload["target_id"], student.id)

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

    def _range_booking(self, suffix="031", start=time(20), end=time(21)):
        self.session.booking_mode = "time_range"
        self.session.start_time = time(17)
        self.session.duration_minutes = 300
        self.session.save()
        AutoSendConfig.objects.get_or_create(
            tenant=self.tenant, trigger="clinic_reminder",
            defaults={"enabled": True, "minutes_before": 30},
        )
        return SessionParticipant.objects.create(
            tenant=self.tenant, session=self.session,
            student=self._student(suffix, "예약학생"),
            status=SessionParticipant.Status.BOOKED,
            booking_start_time=start, booking_end_time=end,
        )

    def _at(self, hour, minute=0):
        return timezone.make_aware(datetime(2026, 5, 15, hour, minute))

    @patch("apps.domains.messaging.services.notification_service.send_event_notification", return_value=True)
    def test_range_reminder_uses_actual_booking_start_not_opening(self, send):
        participant = self._range_booking()
        early = send_due_clinic_reminders(now=self._at(16, 30))
        self.assertEqual(early["attempted"], 0)
        send.assert_not_called()
        due = send_due_clinic_reminders(now=self._at(19, 30))
        self.assertEqual(due["attempted"], 1)
        self.assertEqual(due["sent"], 1)
        self.assertEqual(send.call_args.kwargs["student"], participant.student)
        self.assertEqual(send.call_args.kwargs["send_to"], "student")
        self.assertEqual(send.call_args.kwargs["context"]["시간"], "20:00")

    @patch("apps.domains.messaging.services.notification_service.send_event_notification", return_value=True)
    def test_range_reminder_only_sends_due_uncancelled_bookings(self, send):
        due = self._range_booking("032")
        self._range_booking("033", start=time(20, 30), end=time(21))
        cancelled = self._range_booking("034")
        cancelled.status = SessionParticipant.Status.CANCELLED
        cancelled.save()
        result = send_due_clinic_reminders(now=self._at(19, 32))
        self.assertEqual(result["sent"], 1)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["student"], due.student)

    @patch("apps.domains.messaging.services.notification_service.send_event_notification")
    def test_range_reminder_dry_run_counts_due_without_dispatch(self, send):
        self._range_booking()
        result = send_due_clinic_reminders(now=self._at(19, 30), dry_run=True)
        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["sessions_due"], 1)
        send.assert_not_called()

    @patch("apps.domains.messaging.services.notification_service.send_event_notification")
    def test_range_reminder_never_replays_old_due_window(self, send):
        self._range_booking()
        result = send_due_clinic_reminders(now=self._at(19, 36))
        self.assertEqual(result["attempted"], 0)
        send.assert_not_called()

    @patch("apps.domains.messaging.services.notification_service.send_event_notification")
    def test_range_reminder_does_not_guess_missing_booking_time(self, send):
        participant = self._range_booking()
        participant.booking_start_time = None
        participant.booking_end_time = None
        participant.save()
        result = send_due_clinic_reminders(now=self._at(16, 30))
        self.assertEqual(result["attempted"], 0)
        send.assert_not_called()

    @patch("apps.domains.messaging.services.notification_service.send_event_notification")
    def test_range_reminder_rejects_foreign_student_relation(self, send):
        participant = self._range_booking()
        foreign = Tenant.objects.create(code="foreign-clinic-reminder", name="Foreign")
        Student.objects.filter(pk=participant.student_id).update(tenant=foreign)
        result = send_due_clinic_reminders(now=self._at(19, 30))
        self.assertEqual(result["attempted"], 0)
        send.assert_not_called()

    @patch("apps.domains.messaging.policy.get_owner_tenant_id")
    @patch("apps.domains.messaging.policy.is_messaging_disabled", return_value=False)
    def test_range_reminder_repeat_tick_preserves_one_durable_occurrence(self, _disabled, owner):
        owner.return_value = self.tenant.id
        participant = self._range_booking()
        template = MessageTemplate.objects.create(
            tenant=self.tenant, category="clinic", name="예약 시작 안내",
            body="#{시간} 클리닉이 곧 시작됩니다.",
        )
        AutoSendConfig.objects.filter(tenant=self.tenant, trigger="clinic_reminder").update(template=template)
        with patch("django.utils.timezone.now", return_value=self._at(19, 30)):
            first = send_due_clinic_reminders(now=self._at(19, 30))
            second = send_due_clinic_reminders(now=self._at(19, 31))
        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["attempted"], 0)
        self.assertEqual(second["deduplicated"], 1)
        outbox = ScheduledNotification.objects.get(tenant=self.tenant, trigger="clinic_reminder")
        self.assertEqual(outbox.origin_id, f"clinic_booking:{participant.id}:{self.session.id}:20260515:2000")
        self.assertEqual(outbox.payload["target_type"], "student")
        self.assertIn("20:00", outbox.payload["text"])

    def test_range_pending_dispatch_rechecks_cancellation_time_and_tenant(self):
        from apps.domains.messaging.scheduled import _clinic_participant_reminder_is_stale
        from types import SimpleNamespace

        participant = self._range_booking()
        notification = SimpleNamespace(
            trigger="clinic_reminder", tenant_id=self.tenant.id,
            origin_id=f"clinic_booking:{participant.id}:{self.session.id}:20260515:2000",
        )
        with patch("django.utils.timezone.now", return_value=self._at(19, 30)):
            self.assertFalse(_clinic_participant_reminder_is_stale(notification))
            participant.status = SessionParticipant.Status.CANCELLED
            participant.save(update_fields=["status"])
            self.assertTrue(_clinic_participant_reminder_is_stale(notification))
            participant.status = SessionParticipant.Status.BOOKED
            participant.booking_start_time = time(20, 30)
            participant.save(update_fields=["status", "booking_start_time"])
            self.assertTrue(_clinic_participant_reminder_is_stale(notification))
            participant.booking_start_time = time(20)
            participant.save(update_fields=["booking_start_time"])
            notification.tenant_id += 1000
            self.assertTrue(_clinic_participant_reminder_is_stale(notification))
        notification.tenant_id = self.tenant.id
        with patch("django.utils.timezone.now", return_value=self._at(20, 1)):
            self.assertTrue(_clinic_participant_reminder_is_stale(notification))
