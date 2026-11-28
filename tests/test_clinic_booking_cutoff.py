import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import override_settings
from django.utils import timezone
from jsonschema import Draft7Validator
from rest_framework.test import APITestCase

from apps.core.models import TenantMembership
from apps.domains.clinic.booking_cutoff import booking_cutoff_at
from apps.domains.clinic.contracts import booking_notice_is_current
from apps.domains.clinic.models import SessionParticipant
from apps.domains.clinic.tests import ClinicAPITestMixin
from apps.domains.messaging.default_templates import DEFAULT_TEMPLATES
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, NotificationLog, ScheduledNotification
from apps.domains.messaging.clinic_booking_notices import booking_notice_body


class ClinicBookingCutoffTest(APITestCase, ClinicAPITestMixin):
    def setUp(self):
        data = self.setup_api_tenant("clinic_deadline", student_count=2)
        self.admin = data["admin_user"]
        self.admin.phone = "01011112222"
        self.admin.save(update_fields=["phone"])
        self.tenant = data["tenant"]
        self.day = timezone.localdate() + datetime.timedelta(days=3)
        self.session = self.make_clinic_session(self.tenant, date=self.day, start_time=datetime.time(15))
        self.student, self.other_student = data["students"]
        self.student.phone = "01055556666"
        self.student.parent_phone = "01077778888"
        self.student.save(update_fields=["phone", "parent_phone"])
        self.headers = self._headers(self.tenant)

    def _settings(self, **fields):
        self.client.force_authenticate(self.admin)
        response = self.client.patch("/api/v1/clinic/settings/", fields, format="json", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.tenant.refresh_from_db()
        return response.data

    def _reserve(self, **kwargs):
        self.client.force_authenticate(self.student.user)
        return self.client.post("/api/v1/clinic/participants/", {"session": self.session.id, **kwargs},
                                format="json", **self.headers)

    def _prepare_notices(self, body):
        template = MessageTemplate.objects.create(tenant=self.tenant, category="clinic", name="예약", body=body)
        AutoSendConfig.objects.create(tenant=self.tenant, trigger="clinic_reservation_created",
                                      template=template, enabled=True, message_mode="alimtalk", delay_mode="immediate")

    def test_settings_openapi_matches_partial_updates_and_role_rejections(self):
        url = "/api/v1/clinic/settings/"
        spec = json.loads((Path(__file__).resolve().parents[1] / "schema/openapi.json").read_text("utf-8"))
        operations = spec["paths"][url]

        def validate(body_schema, data):
            Draft7Validator({"components": spec["components"], **body_schema}).validate(data)

        def response(method, reply):
            body = operations[method]["responses"][str(reply.status_code)]["content"]["application/json"]["schema"]
            validate(body, reply.json())
            return reply.json()

        request = operations["patch"]["requestBody"]["content"]["application/json"]["schema"]
        writable = spec["components"]["schemas"][request["$ref"].split("/")[-1]]
        self.assertFalse(writable.get("required"))
        self.assertFalse({"saved_colors", "multi_slot_booking_default", "capabilities"} & writable["properties"].keys())
        self.client.force_authenticate(self.admin)
        initial = response("get", self.client.get(url, **self.headers))
        cutoff = {"booking_cutoff_mode": "previous_day", "booking_cutoff_time": "19:30",
                  "booking_cutoff_minutes": 90, "allow_late_requests": True}
        legacy = {"use_daily_random": False, "colors": ["#123456", "#234567", "#345678"]}

        def patch_and_reload(update):
            validate(request, update)
            saved = self.client.patch(url, update, format="json", **self.headers)
            self.assertEqual(saved.status_code, 200, saved.data)
            payload = response("patch", saved)
            self.assertEqual({k: payload[k] for k in update}, update)
            self.assertEqual(payload["saved_colors"], legacy["colors"])
            self.assertEqual(payload, response("get", self.client.get(url, **self.headers)))
            return payload

        saved_legacy = patch_and_reload(legacy)
        self.assertEqual({k: saved_legacy[k] for k in cutoff}, {k: initial[k] for k in cutoff})
        payload = patch_and_reload(cutoff)
        self.assertEqual(set(payload), set(writable["properties"]) | {"capabilities", "saved_colors", "multi_slot_booking_default"})
        self.assertTrue(payload["capabilities"]["booking_policy"]["write"])
        invalid = self.client.patch(url, {"allow_late_requests": False, "booking_cutoff_time": "19:30:01"},
                                    format="json", **self.headers)
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("booking_cutoff_time", response("patch", invalid))
        teacher = self.make_user("clinic_settings_reader")
        TenantMembership.ensure_active(tenant=self.tenant, user=teacher, role="teacher")
        self.client.force_authenticate(teacher)
        teacher_settings = response("get", self.client.get(url, **self.headers))
        self.assertEqual(teacher_settings["capabilities"]["booking_policy"], {"read": True, "write": False})
        allowed = self.client.patch(url, {"use_daily_random": False}, format="json", **self.headers)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(teacher_settings, response("patch", allowed))
        denied = self.client.patch(url, {"allow_late_requests": False}, format="json", **self.headers)
        self.assertEqual(denied.status_code, 403)
        self.assertIn("detail", response("patch", denied))
        self.client.force_authenticate(self.admin)
        self.assertEqual(payload, response("get", self.client.get(url, **self.headers)))

    def test_late_range_request_persists_then_approval_has_separate_outbox(self):
        self._settings(booking_cutoff_mode="previous_day", booking_cutoff_time="21:00",
                       allow_late_requests=True, auto_approve_booking=True)
        self.session.booking_mode = "time_range"
        self.session.duration_minutes = 420
        self.session.notification_teacher = self.admin
        self.session.save()
        self._prepare_notices(DEFAULT_TEMPLATES["clinic_reservation_created"]["body"])
        now = timezone.make_aware(datetime.datetime.combine(self.day, datetime.time(14)))
        with override_settings(OWNER_TENANT_ID=self.tenant.id), patch("django.utils.timezone.now", return_value=now):
            response = self._reserve(booking_start_time="17:00", booking_end_time="19:00")
            self.assertEqual(response.status_code, 201, response.data)
            participant = SessionParticipant.objects.get(pk=response.data["id"])
            self.assertEqual(participant.status, "pending")
            self.assertIs(response.data["requested_after_cutoff"], True)
            self.assertTrue(participant.requested_after_cutoff)
            rows = list(ScheduledNotification.objects.all())
            self.assertCountEqual([row.payload["target_type"] for row in rows], ["parent", "student", "clinic_teacher"])
            for row in rows:
                slots = {r["key"]: r["value"] for r in row.payload["alimtalk_replacements"]}
                self.assertEqual(slots["클리닉시간"], "17:00–19:00")
                self.assertIn("마감 후", slots["선생님메모"])
                self.assertIn("승인 대기", slots["선생님메모"])
            reference = next(row.payload for row in rows if row.payload["target_type"] == "clinic_teacher")
            self.assertTrue(booking_notice_is_current(reference, self.tenant.pk))
            self.session.notification_teacher = None
            self.session.save(update_fields=["notification_teacher"])
            self.assertFalse(booking_notice_is_current(reference, self.tenant.pk))
            self.session.notification_teacher = self.admin
            self.session.save(update_fields=["notification_teacher"])
            self.client.force_authenticate(self.admin)
            approved = self.client.patch(f"/api/v1/clinic/participants/{participant.pk}/set_status/",
                                         {"status": "booked"}, format="json", **self.headers)
            self.assertEqual(approved.status_code, 200, approved.data)
            participant.refresh_from_db()
            self.assertEqual(participant.status, "booked")
            self.assertFalse(booking_notice_is_current(reference, self.tenant.pk))
            approvals = [row.payload for row in ScheduledNotification.objects.all()
                         if row.payload["domain_object_id"].endswith(":approval")]
            self.assertCountEqual([p["target_type"] for p in approvals], ["student", "parent"])
            self.assertTrue(all("승인하여" in p["text"] for p in approvals))

    def test_exact_boundary_blocks_student_and_bulk_but_staff_can_add(self):
        self._settings(booking_cutoff_mode="before_start", booking_cutoff_minutes=120,
                       allow_late_requests=False, auto_approve_booking=True)
        start = datetime.datetime.combine(self.day, self.session.start_time)
        deadline = booking_cutoff_at(self.tenant, start)
        with patch("django.utils.timezone.now", return_value=deadline):
            response = self._reserve()
            self.assertEqual(response.status_code, 400, response.data)
            bulk = self.client.post("/api/v1/clinic/participants/bulk-create/",
                                    {"session_ids": [self.session.id]}, format="json", **self.headers)
            self.assertEqual(bulk.status_code, 400, bulk.data)
            self.assertFalse(self.session.participants.exists())
            self.client.force_authenticate(self.admin)
            direct = self.client.post("/api/v1/clinic/participants/",
                                      {"session": self.session.pk, "student": self.other_student.pk},
                                      format="json", **self.headers)
            self.assertEqual(direct.status_code, 201, direct.data)
            self.assertEqual(direct.data["status"], "booked")
        with patch("django.utils.timezone.now", return_value=deadline - datetime.timedelta(seconds=1)):
            response = self._reserve()
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(response.data["status"], "booked")

    def test_session_recipient_requires_active_tenant_staff_and_does_not_leak_to_student(self):
        foreign = self.make_user("foreign_teacher")
        foreign.phone = "01099998888"
        foreign.save(update_fields=["phone"])
        TenantMembership.ensure_active(tenant=self.make_tenant("other_clinic"), user=foreign, role="teacher")
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/clinic/sessions/{self.session.pk}/"
        choices = self.client.get("/api/v1/clinic/sessions/notification-teachers/", **self.headers)
        self.assertEqual([row["id"] for row in choices.data], [self.admin.pk])
        for recipient, code in ((foreign.pk, 400), (self.student.user_id, 400), (self.admin.pk, 200)):
            reply = self.client.patch(url, {"notification_teacher_id": recipient}, format="json", **self.headers)
            self.assertEqual(reply.status_code, code, reply.data)
        self.assertEqual(self.client.get(url, **self.headers).data["notification_teacher_id"], self.admin.pk)
        self.client.force_authenticate(self.student.user)
        self.assertNotIn("notification_teacher_id", self.client.get(url, **self.headers).data)
        self.assertEqual(self.client.get("/api/v1/clinic/sessions/notification-teachers/", **self.headers).status_code, 403)
        self.assertEqual(self.client.patch("/api/v1/clinic/settings/", {"allow_late_requests": True},
                                           format="json", **self.headers).status_code, 403)

    @override_settings(CLINIC_OVERNIGHT_TIME_RANGE_WRITES_ENABLED=True)
    def test_previous_day_uses_actual_date_and_preserves_custom_memo(self):
        self._settings(booking_cutoff_mode="previous_day", booking_cutoff_time="21:00", auto_approve_booking=True)
        self.session.booking_mode = "time_range"
        self.session.start_time = datetime.time(20)
        self.session.duration_minutes = 420
        self.session.save()
        self._prepare_notices("상태는 #{예약상태}입니다. 필기구를 준비해 주세요.")
        now = timezone.make_aware(datetime.datetime.combine(self.day, datetime.time(20, 1)))
        with override_settings(OWNER_TENANT_ID=self.tenant.id), patch("django.utils.timezone.now", return_value=now):
            response = self._reserve(booking_start_time="01:00", booking_end_time="02:00")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "booked")
        rows = list(ScheduledNotification.objects.all())
        self.assertCountEqual([r.payload["target_type"] for r in rows], ["student", "parent"])
        values = {r["key"]: r["value"] for r in rows[0].payload["alimtalk_replacements"]}
        self.assertEqual(values["클리닉날짜"], (self.day + datetime.timedelta(days=1)).isoformat())
        self.assertEqual(values["클리닉시간"], "01:00–02:00")
        self.assertEqual(values["선생님메모"], "상태는 예약 확정입니다. 필기구를 준비해 주세요.")
        self.assertEqual(booking_notice_body("필기구 지참", "pending"), "승인 대기\n필기구 지참")

    def test_change_to_closed_session_preserves_original_until_late_request_is_enabled(self):
        self._settings(booking_cutoff_mode="before_start", booking_cutoff_minutes=60, auto_approve_booking=True)
        original = self.make_clinic_session(self.tenant, date=self.day + datetime.timedelta(days=1))
        participant = self.make_participant(self.tenant, original, self.student, status="pending")
        now = timezone.make_aware(datetime.datetime.combine(self.day, datetime.time(14, 5)))
        url = f"/api/v1/clinic/participants/{participant.pk}/change-booking/"
        payload = {"new_session_id": self.session.pk}
        with patch("django.utils.timezone.now", return_value=now):
            self.client.force_authenticate(self.student.user)
            closed = self.client.post(url, payload, format="json", **self.headers)
            self.assertEqual(closed.status_code, 400, closed.data)
            participant.refresh_from_db()
            self.assertEqual(participant.status, "pending")
            self.assertFalse(self.session.participants.exists())
            self._settings(allow_late_requests=True)
            self.client.force_authenticate(self.student.user)
            changed = self.client.post(url, payload, format="json", **self.headers)
            self.assertIn(changed.status_code, (200, 201), changed.data)
            participant.refresh_from_db()
            self.assertEqual(participant.status, "cancelled")
            self.assertEqual(self.session.participants.get().status, "pending")

    def test_teacher_reference_consumed_once_even_on_redelivery(self):
        from apps.domains.messaging.alimtalk_content_builders import SOLAPI_CLINIC_INFO
        from apps.worker.messaging_worker import sqs_main

        self.session.notification_teacher = self.admin
        self.session.save(update_fields=["notification_teacher"])
        self._prepare_notices("예약 상태 #{예약상태}")
        queue = MagicMock()
        queue.send_message.return_value = True
        channel = "QA-CLINIC-CHANNEL"
        cfg = SimpleNamespace(MESSAGING_SQS_QUEUE_NAME="clinic-reference-fixture", SQS_WAIT_TIME_SECONDS=0,
                              TEST_TENANT_ID=9999, OWNER_TENANT_ID=self.tenant.id, SOLAPI_SENDER="0212345678",
                              SOLAPI_KAKAO_PF_ID=channel)

        def accept_at_adapter(config, *, before_provider_call, **options):
            self.assertTrue(before_provider_call())
            self.assertEqual(options["template_id"], SOLAPI_CLINIC_INFO)
            return {"status": "ok", "group_id": "synthetic-acceptance"}

        with (
            override_settings(OWNER_TENANT_ID=self.tenant.id, SOLAPI_KAKAO_PF_ID=channel,
                              MESSAGING_TENANT_BINDING_KEY="local-clinic-contract"),
            patch("apps.domains.messaging.sqs_queue.get_queue_client", return_value=queue),
            patch("apps.domains.messaging.policy.check_recipient_allowed", return_value=True),
            patch("academy.adapters.compute.ec2_control.ensure_messaging_worker_asg_min_capacity"),
            patch.object(sqs_main, "send_one_alimtalk", side_effect=accept_at_adapter) as provider,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                result = self._reserve()
            self.assertEqual(result.status_code, 201, result.data)
            messages = [item.kwargs["message"] for item in queue.send_message.call_args_list]
            self.assertCountEqual([m["target_type"] for m in messages], ["parent", "student", "clinic_teacher"])
            teacher = next(m for m in messages if m["target_type"] == "clinic_teacher")
            deliveries = iter(enumerate([*messages, teacher]))

            def receive(**kwargs):
                item = next(deliveries, None)
                if item is None:
                    sqs_main._shutdown = True
                    return None
                index, message = item
                return {"Body": json.dumps(message), "ReceiptHandle": str(index), "MessageId": str(index)}

            queue.receive_message.side_effect = receive
            with (
                patch.object(sqs_main, "_shutdown", False),
                patch.object(sqs_main, "load_config", return_value=cfg),
                patch.object(sqs_main, "get_queue_client", return_value=queue),
                patch.object(sqs_main, "acquire_job_lock", return_value=True),
                patch.object(sqs_main, "release_job_lock"), patch.object(sqs_main, "_record_progress"),
                patch.object(sqs_main.signal, "signal"),
            ):
                self.assertEqual(sqs_main.main(), 0)
        self.assertEqual(provider.call_count, 3)
        log = NotificationLog.objects.get(target_type="clinic_teacher", success=True)
        self.assertEqual(log.business_idempotency_key, teacher["business_idempotency_key"])
