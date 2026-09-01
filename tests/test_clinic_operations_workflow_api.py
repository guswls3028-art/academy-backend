import datetime
from unittest.mock import patch

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.domains.clinic.services.lifecycle import build_clinic_reminder_send_times
from apps.domains.clinic.tests import ClinicAPITestMixin
from apps.domains.messaging.models import ScheduledNotification
from apps.domains.messaging.models import AutoSendConfig
from apps.domains.messaging.alimtalk_content_builders import get_template_type
from apps.domains.messaging.management.commands.cleanup_dead_message_triggers import (
    DEAD_TRIGGERS,
)
from apps.domains.messaging.policy import (
    get_trigger_implementation_status,
    get_trigger_policy,
    requires_template_ready_opt_in,
)
from apps.support.clinic.session_dependencies import send_clinic_reminder_for_participant


KST = datetime.timezone(datetime.timedelta(hours=9))


class ClinicOperationsWorkflowAPITest(APITestCase, ClinicAPITestMixin):
    def setUp(self):
        self.data = self.setup_api_tenant("clinic_operations_workflow", student_count=1)
        self.tenant = self.data["tenant"]
        self.admin = self.data["admin_user"]
        self.student = self.data["students"][0]
        self.client.force_authenticate(user=self.admin)

    def test_staff_can_restore_no_show_as_on_time_arrival(self):
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="no_show",
        )
        participant.completed_at = timezone.now()
        participant.completed_by = self.admin
        participant.save(update_fields=["completed_at", "completed_by"])

        with patch(
            "apps.domains.clinic.views.participant_views._send_clinic_notification",
            return_value={"requested": 1, "failed": 0},
        ) as notify:
            response = self.client.patch(
                f"/api/v1/clinic/participants/{participant.id}/set_status/",
                {"status": "attended", "is_late": False, "send_to": "parent"},
                format="json",
                **self._headers(self.tenant),
            )

        self.assertEqual(response.status_code, 200, response.data)
        participant.refresh_from_db()
        self.assertEqual(participant.status, "attended")
        self.assertIsNotNone(participant.checked_in_at)
        self.assertFalse(participant.is_late)
        self.assertIsNotNone(participant.completed_at)
        self.assertEqual(participant.completed_by_id, self.admin.id)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["send_to"], "parent")

    def test_staff_can_restore_no_show_as_late_arrival(self):
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="no_show",
        )

        with patch(
            "apps.domains.clinic.views.participant_views._send_clinic_notification",
            return_value={"requested": 2, "failed": 0},
        ) as notify:
            response = self.client.patch(
                f"/api/v1/clinic/participants/{participant.id}/set_status/",
                {"status": "attended", "is_late": True, "send_to": "both"},
                format="json",
                **self._headers(self.tenant),
            )

        self.assertEqual(response.status_code, 200, response.data)
        participant.refresh_from_db()
        self.assertEqual(participant.status, "attended")
        self.assertIsNotNone(participant.checked_in_at)
        self.assertTrue(participant.is_late)
        self.assertEqual(response.data["attendance_label"], "지각 등원")
        self.assertEqual(notify.call_args.kwargs["send_to"], "both")

    def test_invalid_recipient_does_not_change_attendance(self):
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="booked",
        )

        response = self.client.patch(
            f"/api/v1/clinic/participants/{participant.id}/set_status/",
            {"status": "attended", "send_to": "everyone"},
            format="json",
            **self._headers(self.tenant),
        )

        self.assertEqual(response.status_code, 400, response.data)
        participant.refresh_from_db()
        self.assertEqual(participant.status, "booked")
        self.assertIsNone(participant.checked_in_at)

    def test_checkout_without_arrival_requires_exact_confirmation_and_is_idempotent(self):
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="booked",
        )

        rejected = self.client.post(
            f"/api/v1/clinic/participants/{participant.id}/checkout/",
            {"send_to": "parent"},
            format="json",
            **self._headers(self.tenant),
        )
        self.assertEqual(rejected.status_code, 400, rejected.data)
        participant.refresh_from_db()
        self.assertIsNone(participant.checked_out_at)

        wrong_session = self.client.post(
            f"/api/v1/clinic/participants/{participant.id}/checkout/",
            {
                "confirm_without_arrival": True,
                "expected_session_id": participant.session_id + 1,
                "expected_student_id": participant.student_id,
            },
            format="json",
            **self._headers(self.tenant),
        )
        self.assertEqual(wrong_session.status_code, 409, wrong_session.data)

        wrong_student = self.client.post(
            f"/api/v1/clinic/participants/{participant.id}/checkout/",
            {
                "confirm_without_arrival": True,
                "expected_session_id": participant.session_id,
                "expected_student_id": participant.student_id + 1,
            },
            format="json",
            **self._headers(self.tenant),
        )
        self.assertEqual(wrong_student.status_code, 409, wrong_student.data)

        with patch(
            "apps.domains.clinic.views.participant_views._send_clinic_notification",
        ) as notify:
            checked_out = self.client.post(
                f"/api/v1/clinic/participants/{participant.id}/checkout/",
                {
                    "confirm_without_arrival": True,
                    "expected_session_id": participant.session_id,
                    "expected_student_id": participant.student_id,
                },
                format="json",
                **self._headers(self.tenant),
            )

        self.assertEqual(checked_out.status_code, 200, checked_out.data)
        self.assertEqual(checked_out.data["checkout_mode"], "arrival_not_recorded")
        self.assertIsNone(checked_out.data["notification"])
        notify.assert_not_called()
        participant.refresh_from_db()
        first_checked_out_at = participant.checked_out_at
        self.assertIsNotNone(first_checked_out_at)
        self.assertIsNone(participant.checked_in_at)
        self.assertEqual(participant.status, "booked")
        self.assertEqual(participant.checkout_mode, "arrival_not_recorded")
        self.assertEqual(participant.checked_out_by_id, self.admin.id)

        duplicate = self.client.post(
            f"/api/v1/clinic/participants/{participant.id}/checkout/",
            {
                "confirm_without_arrival": True,
                "expected_session_id": participant.session_id,
                "expected_student_id": participant.student_id,
            },
            format="json",
            **self._headers(self.tenant),
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.data)
        participant.refresh_from_db()
        self.assertEqual(participant.checked_out_at, first_checked_out_at)

    def test_normal_checkout_preserves_arrival_and_study_completion_and_is_idempotent(self):
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="attended",
        )

        participant.checked_in_at = timezone.now()
        participant.is_late = True
        participant.completed_at = timezone.now() - datetime.timedelta(minutes=10)
        participant.completed_by = self.admin
        participant.save(
            update_fields=[
                "status",
                "checked_in_at",
                "is_late",
                "completed_at",
                "completed_by",
            ]
        )
        completed_at = participant.completed_at

        with patch(
            "apps.domains.clinic.views.participant_views._send_clinic_notification",
        ) as notify:
            checked_out = self.client.post(
                f"/api/v1/clinic/participants/{participant.id}/checkout/",
                {},
                format="json",
                **self._headers(self.tenant),
            )

        self.assertEqual(checked_out.status_code, 200, checked_out.data)
        participant.refresh_from_db()
        self.assertIsNotNone(participant.checked_out_at)
        self.assertEqual(participant.completed_at, completed_at)
        self.assertIsNotNone(participant.checked_in_at)
        self.assertTrue(participant.is_late)
        self.assertEqual(participant.checkout_mode, "arrival_recorded")
        self.assertEqual(checked_out.data["checkout_mode"], "arrival_recorded")
        self.assertIsNone(checked_out.data["notification"])
        notify.assert_not_called()
        first_checked_out_at = participant.checked_out_at

        duplicate = self.client.post(
            f"/api/v1/clinic/participants/{participant.id}/checkout/",
            {},
            format="json",
            **self._headers(self.tenant),
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.data)
        participant.refresh_from_db()
        self.assertEqual(participant.checked_out_at, first_checked_out_at)

    def test_checkout_cannot_cross_tenant_boundary(self):
        foreign = self.setup_api_tenant("clinic_operations_foreign", student_count=1)
        participant = self.make_participant(
            foreign["tenant"],
            foreign["clinic_session"],
            foreign["students"][0],
            status="booked",
        )

        response = self.client.post(
            f"/api/v1/clinic/participants/{participant.id}/checkout/",
            {
                "confirm_without_arrival": True,
                "expected_session_id": participant.session_id,
                "expected_student_id": participant.student_id,
            },
            format="json",
            **self._headers(self.tenant),
        )

        self.assertEqual(response.status_code, 404, response.data)
        participant.refresh_from_db()
        self.assertIsNone(participant.checked_out_at)

    def test_checkout_notification_is_distinct_and_template_fail_closed(self):
        self.assertEqual(AutoSendConfig.Trigger.CLINIC_CHECK_OUT, "clinic_check_out")
        self.assertEqual(get_trigger_policy("clinic_check_out"), "AUTO_DEFAULT")
        self.assertEqual(get_trigger_implementation_status("clinic_check_out"), "implemented")
        self.assertTrue(requires_template_ready_opt_in("clinic_check_out"))
        self.assertIsNone(get_template_type("clinic_check_out"))
        self.assertNotIn("clinic_check_out", DEAD_TRIGGERS)

    def test_checkout_without_approved_template_queues_zero_messages(self):
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="attended",
        )
        participant.checked_in_at = timezone.now() - datetime.timedelta(hours=1)
        participant.save(update_fields=["checked_in_at"])

        response = self.client.post(
            f"/api/v1/clinic/participants/{participant.id}/checkout/",
            {"send_to": "parent"},
            format="json",
            **self._headers(self.tenant),
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["notification"])
        participant.refresh_from_db()
        self.assertIsNotNone(participant.checked_out_at)

    def test_staff_can_create_replacement_booking_from_confirmed_absence(self):
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="no_show",
        )
        replacement_session = self.make_clinic_session(
            self.tenant,
            date=self.data["clinic_session"].date + datetime.timedelta(days=1),
            start_time=datetime.time(16, 0),
            location="102호",
        )

        with patch(
            "apps.domains.clinic.views.participant_views._send_clinic_notification",
            return_value={"requested": 1, "failed": 0},
        ):
            response = self.client.post(
                f"/api/v1/clinic/participants/{participant.id}/change-booking/",
                {
                    "new_session_id": replacement_session.id,
                    "memo": "결석 후 보충 일정 이동",
                    "send_to": "parent",
                },
                format="json",
                **self._headers(self.tenant),
            )

        self.assertEqual(response.status_code, 200, response.data)
        participant.refresh_from_db()
        self.assertEqual(participant.status, "no_show")
        self.assertEqual(response.data["session"], replacement_session.id)
        self.assertEqual(response.data["status"], "booked")

    @patch(
        "apps.domains.clinic.views.participant_views.send_clinic_reminder_for_participant",
        return_value={"status": "ok", "sent": 2, "scheduled": 6, "skipped": 0},
    )
    def test_repeat_reminder_api_accepts_recipient_interval_and_end_time(self, remind):
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="booked",
        )

        response = self.client.post(
            f"/api/v1/clinic/participants/{participant.id}/remind/",
            {
                "mode": "repeat",
                "send_to": "both",
                "interval_minutes": 60,
                "repeat_until": "2026-08-23T21:00:00+09:00",
            },
            format="json",
            **self._headers(self.tenant),
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["sent"], 2)
        self.assertEqual(response.data["scheduled"], 6)
        kwargs = remind.call_args.kwargs
        self.assertEqual(kwargs["send_to"], "both")
        self.assertEqual(kwargs["repeat_interval_minutes"], 60)
        self.assertEqual(
            timezone.localtime(kwargs["repeat_until"]).strftime("%Y-%m-%d %H:%M"),
            "2026-08-23 21:00",
        )

    @patch(
        "apps.domains.clinic.views.participant_views._send_clinic_notification",
        return_value={"requested": 1, "failed": 0},
    )
    def test_arrival_cancels_only_this_participants_future_reminders(self, _notify):
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="booked",
        )
        future = timezone.now() + datetime.timedelta(hours=1)
        reminder = ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="clinic_reminder",
            send_at=future,
            status=ScheduledNotification.Status.PENDING,
            origin_id=f"clinic_participant:{participant.id}:manual_reminder:plan:next",
            payload={"to": "01012345678", "text": "아직 오지 않았습니다."},
        )
        unrelated = ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="clinic_reminder",
            send_at=future,
            status=ScheduledNotification.Status.PENDING,
            origin_id="clinic_participant:999999:manual_reminder:plan:next",
            payload={"to": "01099999999", "text": "다른 학생"},
        )

        response = self.client.patch(
            f"/api/v1/clinic/participants/{participant.id}/set_status/",
            {"status": "attended", "send_to": "parent"},
            format="json",
            **self._headers(self.tenant),
        )

        self.assertEqual(response.status_code, 200, response.data)
        reminder.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertEqual(reminder.status, ScheduledNotification.Status.CANCELLED)
        self.assertNotIn("to", reminder.payload)
        self.assertNotIn("text", reminder.payload)
        self.assertEqual(unrelated.status, ScheduledNotification.Status.PENDING)

    @patch(
        "apps.domains.messaging.services.notification_service.send_event_notification",
        return_value=True,
    )
    def test_repeat_service_sends_now_and_persists_each_selected_target(self, send_event):
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="booked",
        )
        now = datetime.datetime(2026, 8, 23, 18, 0, tzinfo=KST)
        until = datetime.datetime(2026, 8, 23, 20, 0, tzinfo=KST)

        result = send_clinic_reminder_for_participant(
            tenant_id=self.tenant.id,
            participant_id=participant.id,
            actor_id=self.admin.id,
            send_to="both",
            repeat_interval_minutes=60,
            repeat_until=until,
            now=now,
        )

        self.assertEqual(result, {"status": "ok", "sent": 2, "scheduled": 4, "skipped": 0})
        self.assertEqual(send_event.call_count, 6)
        targets = [call.kwargs["send_to"] for call in send_event.call_args_list]
        self.assertEqual(targets.count("student"), 3)
        self.assertEqual(targets.count("parent"), 3)
        scheduled_calls = [
            call for call in send_event.call_args_list if call.kwargs.get("send_at")
        ]
        self.assertEqual(len(scheduled_calls), 4)


class ClinicReminderScheduleContractTest(APITestCase):
    def test_repeat_schedule_uses_custom_interval_and_stops_by_22(self):
        now = datetime.datetime(2026, 8, 23, 18, 0, tzinfo=KST)
        until = datetime.datetime(2026, 8, 23, 21, 0, tzinfo=KST)

        send_times = build_clinic_reminder_send_times(
            now=now,
            interval_minutes=60,
            repeat_until=until,
        )

        self.assertEqual(
            send_times,
            [
                datetime.datetime(2026, 8, 23, 19, 0, tzinfo=KST),
                datetime.datetime(2026, 8, 23, 20, 0, tzinfo=KST),
                datetime.datetime(2026, 8, 23, 21, 0, tzinfo=KST),
            ],
        )

    def test_repeat_schedule_rejects_after_22_and_cross_day(self):
        now = datetime.datetime(2026, 8, 23, 18, 0, tzinfo=KST)

        with self.assertRaises(ValidationError):
            build_clinic_reminder_send_times(
                now=now,
                interval_minutes=60,
                repeat_until=datetime.datetime(2026, 8, 23, 22, 1, tzinfo=KST),
            )

        with self.assertRaises(ValidationError):
            build_clinic_reminder_send_times(
                now=now,
                interval_minutes=60,
                repeat_until=datetime.datetime(2026, 8, 24, 20, 0, tzinfo=KST),
            )

    def test_repeat_schedule_rejects_too_frequent_or_excessive_plan(self):
        now = datetime.datetime(2026, 8, 23, 18, 0, tzinfo=KST)
        until = datetime.datetime(2026, 8, 23, 21, 0, tzinfo=KST)

        with self.assertRaises(ValidationError):
            build_clinic_reminder_send_times(
                now=now,
                interval_minutes=5,
                repeat_until=until,
            )

        with self.assertRaises(ValidationError):
            build_clinic_reminder_send_times(
                now=datetime.datetime(2026, 8, 23, 8, 0, tzinfo=KST),
                interval_minutes=10,
                repeat_until=until,
            )
