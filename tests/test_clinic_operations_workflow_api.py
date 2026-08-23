import datetime
from unittest.mock import patch

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.domains.clinic.services.lifecycle import build_clinic_reminder_send_times
from apps.domains.clinic.tests import ClinicAPITestMixin


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
        self.assertIsNone(participant.completed_at)
        self.assertIsNone(participant.completed_by_id)
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

    def test_checkout_requires_arrival_and_keeps_arrival_classification(self):
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="booked",
        )

        rejected = self.client.post(
            f"/api/v1/clinic/participants/{participant.id}/complete/",
            {"send_to": "parent"},
            format="json",
            **self._headers(self.tenant),
        )
        self.assertEqual(rejected.status_code, 400, rejected.data)

        participant.status = "attended"
        participant.checked_in_at = timezone.now()
        participant.is_late = True
        participant.save(update_fields=["status", "checked_in_at", "is_late"])

        with patch(
            "apps.domains.clinic.views.participant_views._send_clinic_notification",
            return_value={"requested": 1, "failed": 0},
        ) as notify:
            checked_out = self.client.post(
                f"/api/v1/clinic/participants/{participant.id}/complete/",
                {"send_to": "parent"},
                format="json",
                **self._headers(self.tenant),
            )

        self.assertEqual(checked_out.status_code, 200, checked_out.data)
        participant.refresh_from_db()
        self.assertIsNotNone(participant.completed_at)
        self.assertIsNotNone(participant.checked_in_at)
        self.assertTrue(participant.is_late)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["send_to"], "parent")

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
