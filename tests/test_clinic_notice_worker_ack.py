"""Business API and real consumer loop with in-memory queue transport and Solapi mock."""

import json
import os
import sys
from collections import deque
from datetime import time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from apps.core.management.commands import setup_ymath_realuse_scenario as fixture
from apps.core.management.commands import verify_development_wrong_notes as command
from apps.core.models import OpsAuditLog, Tenant
from apps.core.services import clinic_notice_development_scenario as clinic
from apps.core.services.development_qa_ownership import WRONG_NOTE_SETUP_SCHEMA
from apps.core.tests.test_development_qa_ai_cleanup import MemoryRedis
from apps.domains.clinic.models import SessionParticipant
from apps.domains.clinic.tests import ClinicAPITestMixin
from apps.domains.messaging.alimtalk_content_builders import SOLAPI_CLINIC_INFO
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, NotificationLog, ScheduledNotification
from apps.worker.messaging_worker import qa_ack, sqs_main
from libs.queue.client import SQSQueueClient


class LocalClinicQueue:
    prefix = "development.qa.clinic-notice."
    release = "sha-" + "b" * 40 + "-run-123-1"

    def bind_transport(self):
        self.cfg = SimpleNamespace(OWNER_TENANT_ID=self.owner.pk, MESSAGING_SQS_QUEUE_NAME=qa_ack.QUEUE,
                                   SOLAPI_API_KEY="mock", SOLAPI_API_SECRET="mock", SOLAPI_SENDER="0212345678",
                                   SOLAPI_KAKAO_PF_ID="development-mock-pfid", TEST_TENANT_ID=9999, SQS_WAIT_TIME_SECONDS=0)
        self.enterContext(override_settings(OWNER_TENANT_ID=self.owner.pk, SOLAPI_KAKAO_PF_ID=self.cfg.SOLAPI_KAKAO_PF_ID,
                                            MESSAGING_TENANT_BINDING_KEY="synthetic-clinic-only"))
        self.enterContext(patch.dict(os.environ, {"ACADEMY_RUNTIME_ENV": "development", "SOLAPI_MOCK": "true",
            "ACADEMY_DEVELOPMENT_RELEASE_ID": self.release, "SOLAPI_KAKAO_PF_ID": self.cfg.SOLAPI_KAKAO_PF_ID,
            "DJANGO_SETTINGS_MODULE": "apps.api.config.settings.worker", "DB_NAME": "academy_api_development",
            "DB_USER": "academy_api_development_app", "MESSAGING_SQS_QUEUE_NAME": qa_ack.QUEUE,
            "MESSAGING_DRY_RUN_TRIGGERS": "", "MESSAGING_TEST_WHITELIST": ",".join(clinic.PHONES.values())}))
        self.enterContext(patch.object(sys.modules["__main__"], "__spec__", SimpleNamespace(name="apps.worker.messaging_worker.sqs_main")))
        self.queue = MagicMock()
        self.queue.send_message.return_value = True
        self.enterContext(patch("apps.domains.messaging.sqs_queue.get_queue_client", return_value=self.queue))
        self.enterContext(patch("academy.adapters.compute.ec2_control.ensure_messaging_worker_asg_min_capacity"))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("External connection in a local fixture")))

    def messages(self, offset=0):
        return [{"MessageId": str(uuid4()), "Attributes": {"ApproximateReceiveCount": "1"},
                 "ReceiptHandle": str(uuid4()), "Body": json.dumps(c.kwargs["message"])}
                for c in self.queue.send_message.call_args_list[offset:]]

    def consume(self, messages, *, delete_ok=True):
        pending = deque(messages)
        by_handle = {m["ReceiptHandle"]: m for m in messages}

        def receive(**kwargs):
            if pending:
                return pending.popleft()
            sqs_main._shutdown = True
            return None

        def change_visibility(*, queue_name, receipt_handle, visibility_timeout):
            self.assertEqual((queue_name, visibility_timeout), (qa_ack.QUEUE, 3))
            m = by_handle[receipt_handle]
            self.assertEqual(json.loads(m["Body"])["target_type"], "clinic_teacher")
            pending.append({**m, "ReceiptHandle": str(uuid4()), "Attributes": {"ApproximateReceiveCount": "2"}})
            return True

        self.queue.receive_message.side_effect = receive
        self.queue.change_message_visibility.side_effect = change_visibility
        adapter = SQSQueueClient.__new__(SQSQueueClient)
        adapter.region_name = "fixture"
        adapter.sqs = MagicMock()
        adapter.sqs.get_queue_url.return_value = {"QueueUrl": "fixture-development-clinic"}
        if not delete_ok:
            adapter.sqs.delete_message.side_effect = OSError("synthetic SQS deletion failure")
        self.queue.delete_message.side_effect = adapter.delete_message
        with (patch.object(sqs_main, "_shutdown", False), patch.object(sqs_main.signal, "signal"),
              patch.object(sqs_main, "load_config", return_value=self.cfg), patch.object(sqs_main, "get_queue_client", return_value=self.queue),
              patch.object(sqs_main, "acquire_job_lock", return_value=True), patch.object(sqs_main, "release_job_lock"),
              patch("apps.shared.utils.heartbeat.beat"), patch.object(sqs_main, "_record_progress")):
            self.assertEqual(sqs_main.main(), 0)
        self.assertFalse(pending)


class ClinicNoticeWorkerAckTests(LocalClinicQueue, ClinicAPITestMixin, APITransactionTestCase):
    def setUp(self):
        self.owner = Tenant.objects.create(code="academy-development-owner", name="QA")
        data = self.setup_api_tenant("qa-ymath-realuse-wn-123-1-bbbbbbbbbbbb")
        self.tenant, self.admin = data["tenant"], data["admin_user"]
        self.student, = data["students"]
        self.student.phone, self.student.parent_phone = "01098000001", "01099000001"
        self.student.save(update_fields=["phone", "parent_phone"])
        self.admin.phone = "01097000001"
        self.admin.save(update_fields=["phone"])
        Tenant.objects.filter(pk__in=[self.tenant.pk, self.owner.pk]).update(messaging_is_active=True, messaging_base_price=0)
        self.headers = self._headers(self.tenant)
        self.client.force_authenticate(self.admin)
        saved = self.client.patch("/api/v1/clinic/settings/", {"booking_cutoff_mode": "before_start",
                                  "booking_cutoff_minutes": 10080, "allow_late_requests": True},
                                  format="json", **self.headers)
        self.assertEqual(saved.status_code, 200, saved.data)
        self.session = self.make_clinic_session(self.tenant, date=timezone.localdate() + timedelta(days=2), start_time=time(15))
        self.session.notification_teacher = self.admin
        self.session.created_by = self.admin
        self.session.save(update_fields=["notification_teacher", "created_by"])
        for tenant in (self.tenant, self.owner):
            template = MessageTemplate.objects.create(tenant=tenant, name="QA", category="clinic", body="#{예약상태}",
                                                      solapi_status="APPROVED", solapi_template_id=SOLAPI_CLINIC_INFO)
            AutoSendConfig.objects.create(tenant=tenant, trigger="clinic_reservation_created", enabled=True,
                                          message_mode="alimtalk", delay_mode="immediate", template=template)
        self.bind_transport()
        setup = OpsAuditLog.objects.create(action="development.wrong-note.setup", actor_username="wrong-note-development-runner",
            target_tenant=self.tenant, result="success", payload={"schema": WRONG_NOTE_SETUP_SCHEMA, "tenant_code": self.tenant.code,
                                                                "tenant_id": self.tenant.pk, "owner_sha256": "a" * 64})
        request_id = str(uuid4())
        self.plan = OpsAuditLog.objects.create(action=self.prefix + "plan", actor_username="clinic-notice-development-runner",
            target_tenant=self.tenant, summary=request_id, result="success", payload={"schema": "clinic-notice-worker/v1",
                "setup_id": setup.pk, "release": self.release, "request_id": request_id, "tenant_id": self.tenant.pk,
                "actor_id": self.admin.pk, "session_id": self.session.pk, "teacher_id": self.admin.pk,
                "student_id": self.student.pk, "redelivery_role": "clinic_teacher"})

    def reserve(self):
        self.client.force_authenticate(self.student.user)
        return self.client.post("/api/v1/clinic/participants/", {"session": self.session.pk}, format="json", **self.headers)

    def approve(self, participant):
        self.client.force_authenticate(self.admin)
        return self.client.patch(f"/api/v1/clinic/participants/{participant}/set_status/",
                                 {"status": "booked"}, format="json", **self.headers)

    def events(self, suffix):
        return OpsAuditLog.objects.filter(action=self.prefix + suffix, summary=self.plan.summary, actor_username="MESSAGING",
                                          target_tenant=self.tenant)

    def test_approved_template_mock_and_actual_delete_result_for_all_five_business_keys(self):
        created = self.reserve()
        self.assertEqual(created.status_code, 201, created.data)
        participant = created.data["id"]
        self.assertEqual(self.reserve().status_code, 409)
        self.consume(self.messages())
        self.assertCountEqual([e["role"] for e in self.events("ack").values_list("payload", flat=True)],
                              ["student", "parent", "clinic_teacher"])
        self.assertEqual(self.approve(participant).status_code, 200)
        self.assertEqual(self.approve(participant).status_code, 400)
        self.consume(self.messages(offset=3))
        self.assertEqual(SessionParticipant.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(self.queue.delete_message.call_count, 5)
        self.assertEqual(self.queue.change_message_visibility.call_count, 1)
        self.assertEqual(self.events("redelivery").get().payload["receive_count"], 1)
        keys = set()
        for event in self.events("ack"):
            p = event.payload
            self.assertEqual(p["receive_count"], 2 if p["role"] == "clinic_teacher" else 1)
            self.assertEqual(p["deduplicated"], p["role"] == "clinic_teacher")
            self.assertEqual(p["participant_id"], participant)
            log = NotificationLog.objects.get(pk=p["log_id"], tenant=self.owner, source_tenant=self.tenant,
                status="sent", success=True, origin_id=p["origin_id"], sqs_message_id=p["message_id"],
                business_idempotency_key=p["business_key"])
            outbox = ScheduledNotification.objects.get(pk=p["outbox_id"], tenant=self.tenant, origin_id=p["origin_id"],
                                                      status="sent", business_idempotency_key=p["business_key"])
            self.assertEqual(outbox.payload["template_id"], SOLAPI_CLINIC_INFO)
            self.assertEqual(self.events("mock-send").get(payload__log_id=log.pk).payload["provider_message_id"], log.provider_message_id)
            keys.add(p["business_key"])
        self.assertEqual((len(keys), self.events("mock-send").count(), self.events("ack").count()), (5, 5, 5))

    def test_failed_delete_and_out_of_scope_messages_cannot_claim_an_ack(self):
        self.assertEqual(self.reserve().status_code, 201)
        raw = next(m for m in self.messages() if json.loads(m["Body"])["target_type"] == "student")
        body = json.loads(raw["Body"])
        self.consume([raw], delete_ok=False)
        self.assertTrue(self.events("mock-send").exists())
        self.assertFalse(self.events("ack").exists())
        for field, wrong in (("target_id", str(self.admin.pk + 10000)), ("template_id", "unexpected"),
                             ("business_idempotency_key", "invalid")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                qa_ack.development_ack_scope(self.cfg, raw, {**body, field: wrong}, self.owner.pk, self.tenant.pk)
        with patch.dict(os.environ, {"ACADEMY_RUNTIME_ENV": "production"}), self.assertNumQueries(0):
            self.assertIsNone(qa_ack.development_ack_scope(self.cfg, raw, body, self.owner.pk, self.tenant.pk))
        ordinary = Tenant.objects.create(code="ordinary-clinic", name="Ordinary synthetic academy")
        self.assertIsNone(qa_ack.development_ack_scope(self.cfg, raw, {**body, "source_tenant_id": ordinary.pk},
                                                       self.owner.pk, ordinary.pk))
        with patch.dict(os.environ, {"SOLAPI_MOCK": "false"}), self.assertRaises(ValueError):
            qa_ack.development_ack_scope(self.cfg, raw, body, self.owner.pk, self.tenant.pk)
        self.consume([{**raw, "ReceiptHandle": str(uuid4()), "Attributes": {"ApproximateReceiveCount": "2"}}])
        self.assertEqual(self.events("mock-send").count(), 1)
        self.assertTrue(self.events("ack").get().payload["deduplicated"])

class ClinicDevelopmentScenarioTests(LocalClinicQueue, APITransactionTestCase):
    def test_fixed_scenario_login_to_committed_cleanup(self):
        self.owner = Tenant.objects.create(code=fixture.DEVELOPMENT_MESSAGING_OWNER_CODE,
                                           name=fixture.DEVELOPMENT_MESSAGING_OWNER_NAME)
        self.bind_transport()
        redis = MemoryRedis()
        r2 = MagicMock()
        r2.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
        procfs = MagicMock()
        procfs.iterdir.return_value = ()
        procfs.read_text.return_value = ""
        consumed = 0

        def local_request(http, method, url, **kwargs):
            nonlocal consumed
            base = "http://127.0.0.1:8000"
            self.assertTrue(url.startswith(base + "/api/v1/"))
            headers = {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": http.headers["X-Tenant-Code"]}
            if "Authorization" in http.headers:
                headers["HTTP_AUTHORIZATION"] = http.headers["Authorization"]
            client = APIClient()
            route = url[len(base):]
            if method == "GET":
                response = client.get(route, kwargs.get("params"), **headers)
            else:
                response = getattr(client, method.lower())(route, kwargs.get("json"), format="json", **headers)
            messages = self.messages(offset=consumed)
            if messages:
                self.consume(messages)
                consumed += len(messages)
            return response

        code = "qa-ymath-realuse-wn-123-1-eeeeeeeeeeee"
        password, capability = "local-synthetic-login", "c" * 64
        with (patch.object(fixture, "_is_persistent_development_runtime", return_value=True),
              patch.object(command, "check_boundary"), patch.object(clinic, "check_mock"),
              patch("requests.sessions.Session.request", autospec=True, side_effect=local_request),
              patch.dict(os.environ, {"REDIS_HOST": "127.0.0.1", "REDIS_PORT": "6379"}),
              patch("libs.redis.client.get_redis_client", return_value=redis),
              patch("apps.core.services.development_qa_ai_cleanup.get_redis_client", return_value=redis),
              patch.object(fixture.Command, "_r2_storage_client", return_value=r2),
              patch.object(fixture, "Path", return_value=procfs)):
            tenant = command.setup(code, capability, password)
            clinic.mark_scope(tenant, self.release)
            result = clinic.run(tenant, password, release=self.release, production_database="synthetic_production")
            self.assertEqual((result["acknowledged"], result["redelivered"], result["mock_calls"], consumed), (5, 1, 5, 5))
            self.assertEqual(result["source_tenant_id"], tenant.pk)
            self.assertEqual(result["owner_tenant_id"], self.owner.pk)
            self.assertEqual(len({r["business_key"] for r in result["deliveries"]}), 5)
            residue = clinic.cleanup(code, capability)
            self.assertEqual(residue, clinic.cleanup(code, capability))
            self.assertFalse(any(residue.values()), residue)
            self.assertTrue(Tenant.objects.filter(pk=self.owner.pk).exists())
