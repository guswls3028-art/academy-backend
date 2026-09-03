import datetime
import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import TenantMembership
from apps.domains.clinic.models import SessionParticipant
from apps.domains.clinic.services.lifecycle import create_participant
from apps.domains.messaging.models import NotificationLog, ScheduledNotification
from apps.domains.clinic.tests import ClinicAPITestMixin


User = get_user_model()


class ClinicTimeRangePolicyAPITest(APITestCase, ClinicAPITestMixin):
    def setUp(self):
        self.data = self.setup_api_tenant("clinic_time_range", student_count=3)
        self.tenant = self.data["tenant"]
        self.admin = self.data["admin_user"]
        self.students = self.data["students"]
        self.session = self.data["clinic_session"]
        self.session.date = datetime.date.today() + datetime.timedelta(days=1)
        self.session.start_time = datetime.time(9, 0)
        self.session.duration_minutes = 480
        self.session.max_participants = 2
        self.session.booking_mode = "time_range"
        self.session.booking_interval_minutes = 30
        self.session.booking_max_stay_minutes = 180
        self.session.save(update_fields=[
            "date",
            "start_time",
            "duration_minutes",
            "max_participants",
            "booking_mode",
            "booking_interval_minutes",
            "booking_max_stay_minutes",
            "updated_at",
        ])

    def _headers(self):
        return super()._headers(self.tenant)

    def _book(self, student, start="10:00", end="11:30"):
        self.client.force_authenticate(user=student.user)
        return self.client.post(
            "/api/v1/clinic/participants/bulk-create/",
            {
                "session_ids": [self.session.id],
                "booking_start_time": start,
                "booking_end_time": end,
            },
            format="json",
            **self._headers(),
        )

    def test_time_range_requires_aligned_actual_range_and_persists_it(self):
        missing = self._book(self.students[0], start="", end="")
        misaligned = self._book(self.students[0], start="10:15", end="11:15")
        too_long = self._book(self.students[0], start="10:00", end="13:30")
        valid = self._book(self.students[0])

        self.assertEqual(missing.status_code, 400, missing.data)
        self.assertEqual(misaligned.status_code, 400, misaligned.data)
        self.assertEqual(too_long.status_code, 400, too_long.data)
        self.assertEqual(valid.status_code, 201, valid.data)
        participant = SessionParticipant.objects.get(id=valid.data["participants"][0]["id"])
        self.assertEqual(participant.booking_start_time, datetime.time(10, 0))
        self.assertEqual(participant.booking_end_time, datetime.time(11, 30))
        self.assertEqual(valid.data["participants"][0]["booking_start_time"], "10:00:00")
        self.assertEqual(valid.data["participants"][0]["booking_end_time"], "11:30:00")

    def test_time_range_capacity_is_concurrent_per_interval_not_whole_window(self):
        first = self._book(self.students[0], start="10:00", end="11:00")
        second = self._book(self.students[1], start="11:00", end="12:00")
        spanning = self._book(self.students[2], start="10:30", end="11:30")

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(spanning.status_code, 201, spanning.data)

        fourth = self.make_student(self.tenant, "range_fourth", name="시간범위")
        fourth.user.tenant = self.tenant
        fourth.user.save(update_fields=["tenant"])
        TenantMembership.ensure_active(tenant=self.tenant, user=fourth.user, role="student")
        rejected = self._book(fourth, start="10:30", end="11:30")

        self.assertEqual(rejected.status_code, 409, rejected.data)
        self.assertEqual(
            SessionParticipant.objects.filter(tenant=self.tenant, session=self.session).count(),
            3,
        )

    def test_availability_reports_each_interval_without_cross_tenant_data(self):
        self.assertEqual(self._book(self.students[0], start="10:00", end="11:00").status_code, 201)
        self.client.force_authenticate(user=self.students[1].user)

        response = self.client.get(
            f"/api/v1/clinic/sessions/{self.session.id}/availability/",
            **self._headers(),
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["booking_mode"], "time_range")
        self.assertEqual(response.data["interval_minutes"], 30)
        self.assertEqual(response.data["max_stay_minutes"], 180)
        slots = {slot["start_time"]: slot for slot in response.data["slots"]}
        self.assertEqual(slots["10:00"]["remaining_capacity"], 1)
        self.assertEqual(slots["11:00"]["remaining_capacity"], 2)

        foreign = self.setup_api_tenant("clinic_time_range_foreign", student_count=1)
        self.client.force_authenticate(user=foreign["admin_user"])
        denied = self.client.get(
            f"/api/v1/clinic/sessions/{self.session.id}/availability/",
            **super()._headers(foreign["tenant"]),
        )
        self.assertEqual(denied.status_code, 404, denied.data)

    def test_fixed_slot_default_remains_backward_compatible(self):
        fixed = self.make_clinic_session(
            self.tenant,
            date=self.session.date,
            start_time=datetime.time(18, 0),
            location="고정 시간대",
            max_participants=1,
        )
        self.client.force_authenticate(user=self.students[0].user)

        response = self.client.post(
            "/api/v1/clinic/participants/",
            {"session": fixed.id},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, 201, response.data)
        fixed.refresh_from_db()
        participant = SessionParticipant.objects.get(id=response.data["id"])
        self.assertEqual(fixed.booking_mode, "fixed_slot")
        self.assertIsNone(participant.booking_start_time)
        self.assertIsNone(participant.booking_end_time)


class ClinicCapabilityAndContactAPITest(APITestCase, ClinicAPITestMixin):
    def setUp(self):
        self.data = self.setup_api_tenant("clinic_capabilities", student_count=1)
        self.tenant = self.data["tenant"]
        self.student = self.data["students"][0]
        self.student.phone = "010-1111-2222"
        self.student.parent_phone = "010-3333-4444"
        self.student.save(update_fields=["phone", "parent_phone", "updated_at"])
        self.session = self.data["clinic_session"]
        self.session.date = datetime.date.today() + datetime.timedelta(days=1)
        self.session.save(update_fields=["date", "updated_at"])
        self.participant = self.make_participant(
            self.tenant,
            self.session,
            self.student,
            status="booked",
        )

    def _headers(self):
        return super()._headers(self.tenant)

    def _make_role_user(self, role):
        user = User.objects.create_user(username=f"clinic_{role}", password="test1234")
        user.tenant = self.tenant
        user.is_staff = True
        user.save(update_fields=["tenant", "is_staff"])
        TenantMembership.objects.create(tenant=self.tenant, user=user, role=role, is_active=True)
        return user

    def test_all_staff_roles_read_contacts_and_write_student_operations(self):
        role_users = {
            "owner": self._make_role_user("owner"),
            "teacher": self._make_role_user("teacher"),
            "staff": self._make_role_user("staff"),
            "admin": self.data["admin_user"],
        }
        for role, user in role_users.items():
            self.client.force_authenticate(user=user)
            detail = self.client.get(
                f"/api/v1/clinic/participants/{self.participant.id}/",
                **self._headers(),
            )
            self.assertEqual(detail.status_code, 200, (role, detail.data))
            self.assertEqual(detail.data["recipient_contacts"], [
                {"role": "student", "name": self.student.name, "phone": "010-1111-2222"},
                {"role": "parent", "name": f"{self.student.name} 학부모", "phone": "010-3333-4444"},
            ])
            settings = self.client.get("/api/v1/clinic/settings/", **self._headers())
            self.assertEqual(settings.status_code, 200, (role, settings.data))
            self.assertTrue(settings.data["capabilities"]["student_operations"]["read"])
            self.assertTrue(settings.data["capabilities"]["student_operations"]["write"])
            self.assertTrue(settings.data["capabilities"]["student_contacts"]["read"])
            self.assertEqual(
                settings.data["capabilities"]["booking_policy"]["write"],
                role in {"owner", "admin"},
            )

    def test_student_never_receives_staff_contacts_or_capabilities(self):
        self.client.force_authenticate(user=self.student.user)
        detail = self.client.get(
            f"/api/v1/clinic/participants/{self.participant.id}/",
            **self._headers(),
        )
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertNotIn("recipient_contacts", detail.data)
        settings = self.client.get("/api/v1/clinic/settings/", **self._headers())
        self.assertEqual(settings.status_code, 403, settings.data)

    def test_booking_policy_patch_is_owner_admin_only_and_atomic(self):
        teacher = self._make_role_user("teacher")
        self.client.force_authenticate(user=teacher)
        denied = self.client.patch(
            "/api/v1/clinic/settings/",
            {
                "booking_mode": "time_range",
                "booking_interval_minutes": 30,
                "booking_max_stay_minutes": 180,
            },
            format="json",
            **self._headers(),
        )
        self.assertEqual(denied.status_code, 403, denied.data)

        self.client.force_authenticate(user=self.data["admin_user"])
        allowed = self.client.patch(
            "/api/v1/clinic/settings/",
            {
                "booking_mode": "time_range",
                "booking_interval_minutes": 30,
                "booking_max_stay_minutes": 180,
            },
            format="json",
            **self._headers(),
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertEqual(allowed.data["booking_mode"], "time_range")
        self.assertEqual(allowed.data["booking_interval_minutes"], 30)
        self.assertEqual(allowed.data["booking_max_stay_minutes"], 180)


class ClinicNotificationHistoryAndRetryAPITest(APITestCase, ClinicAPITestMixin):
    def setUp(self):
        self.data = self.setup_api_tenant("clinic_notification_history", student_count=1)
        self.tenant = self.data["tenant"]
        self.admin = self.data["admin_user"]
        self.student = self.data["students"][0]
        self.participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="booked",
        )
        self.client.force_authenticate(user=self.admin)

    def _headers(self):
        return super()._headers(self.tenant)

    def _log(self, *, suffix="reservation", status="failed"):
        business_key = f"clinic-retry-{self.participant.id}-{suffix}"
        origin = f"clinic_participant:{self.participant.id}:{suffix}"
        log = NotificationLog.objects.create(
            tenant=self.tenant,
            status=status,
            success=status == "sent",
            message_mode="alimtalk",
            notification_type="clinic_reservation_created",
            business_idempotency_key=business_key,
            origin_type="clinic",
            origin_id=origin,
            target_type="student",
            target_id=str(self.student.id),
            target_name=self.student.name,
        )
        ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="clinic_reservation_created",
            send_at=timezone.now(),
            business_idempotency_key=business_key,
            origin_type="clinic",
            origin_id=origin,
            payload={
                "tenant_id": self.tenant.id,
                "to": self.student.phone or "01011112222",
                "text": "원본 알림톡",
                "message_mode": "alimtalk",
                "template_id": "approved-template",
                "event_type": "clinic_reservation_created",
                "target_type": "student",
                "target_id": self.student.id,
                "target_name": self.student.name,
                "occurrence_key": origin,
                "domain_object_id": origin,
                "origin_id": origin,
            },
        )
        return log

    def test_log_history_filters_by_participant_origin_prefix(self):
        own = self._log()
        self._log(suffix="checkout")
        response = self.client.get(
            "/api/v1/messaging/log/",
            {"origin_id_prefix": f"clinic_participant:{self.participant.id}:"},
            **self._headers(),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertIn(own.origin_id, {item["origin_id"] for item in response.data["results"]})

    def test_failed_retry_is_idempotent_and_sent_or_ambiguous_is_blocked(self):
        failed = self._log()

        def create_retry(*, tenant_id, trigger, payload):
            return ScheduledNotification.objects.create(
                tenant_id=tenant_id,
                trigger=trigger,
                send_at=timezone.now(),
                payload=payload,
                origin_type=payload["origin_type"],
                origin_id=payload["origin_id"],
            )

        url = f"/api/v1/clinic/participants/{self.participant.id}/retry-notification/"
        with patch(
            "apps.domains.messaging.scheduled.dispatch_notification_now",
            side_effect=create_retry,
        ) as dispatch:
            first = self.client.post(url, {"log_id": failed.id}, format="json", **self._headers())
            second = self.client.post(url, {"log_id": failed.id}, format="json", **self._headers())

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["outbox_id"], second.data["outbox_id"])
        self.assertEqual(dispatch.call_count, 1)

        sent = self._log(suffix="sent", status="sent")
        denied = self.client.post(url, {"log_id": sent.id}, format="json", **self._headers())
        self.assertEqual(denied.status_code, 409, denied.data)


class ClinicTimeRangePostgresConcurrencyTest(TransactionTestCase, ClinicAPITestMixin):
    reset_sequences = True

    def setUp(self):
        self.data = self.setup_api_tenant("clinic_time_range_pg", student_count=2)
        self.session = self.data["clinic_session"]
        self.session.date = datetime.date.today() + datetime.timedelta(days=1)
        self.session.booking_mode = "time_range"
        self.session.booking_interval_minutes = 30
        self.session.booking_max_stay_minutes = 180
        self.session.max_participants = 1
        self.session.save(update_fields=[
            "date",
            "booking_mode",
            "booking_interval_minutes",
            "booking_max_stay_minutes",
            "max_participants",
            "updated_at",
        ])

    def test_overlapping_writes_serialize_on_the_session_capacity(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock contract")

        barrier = threading.Barrier(2, timeout=10)
        outcomes = []

        def book(student_id):
            close_old_connections()
            try:
                tenant = type(self.data["tenant"]).objects.get(pk=self.data["tenant"].id)
                student = type(self.data["students"][0]).objects.get(pk=student_id)
                session = type(self.session).objects.get(pk=self.session.id)
                barrier.wait()
                result = create_participant(
                    tenant=tenant,
                    validated_data={
                        "session": session,
                        "student": student,
                        "booking_start_time": datetime.time(10, 0),
                        "booking_end_time": datetime.time(11, 0),
                    },
                )
                outcomes.append(("created", result.participant.id))
            except Exception as exc:  # pragma: no cover - asserted below
                outcomes.append(("error", getattr(exc, "status_code", None)))
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=book, args=(student.id,))
            for student in self.data["students"]
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(outcome[0] for outcome in outcomes), ["created", "error"])
        self.assertIn(("error", 409), outcomes)
        self.assertEqual(
            SessionParticipant.objects.filter(tenant=self.data["tenant"], session=self.session).count(),
            1,
        )
