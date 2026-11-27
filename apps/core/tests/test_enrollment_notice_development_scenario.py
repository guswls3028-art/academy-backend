from hashlib import sha256
from io import StringIO
import os
from unittest.mock import Mock, patch
from uuid import uuid4

from django.core.management.base import CommandError
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.core.models import OpsAuditLog, Tenant
from apps.core.services import enrollment_notice_development_scenario as qa
from apps.domains.enrollment.models import EnrollmentNoticeRecipient, EnrollmentNoticeRegistration
from apps.domains.lectures.test_support import create_lecture_fixture
from apps.domains.messaging.models import NotificationLog, ScheduledNotification


class EnrollmentNoticeDevelopmentTests(TransactionTestCase):
    selected_role = "student"

    def setUp(self):
        self.cap = "a" * 64
        self.owner = Tenant.objects.create(code=qa.DEVELOPMENT_MESSAGING_OWNER_CODE, name="mock owner")
        override = override_settings(OWNER_TENANT_ID=self.owner.pk)
        override.enable()
        self.addCleanup(override.disable)
        self.source = Tenant.objects.create(code="qa-ymath-realuse-wn-1-1-abcdef123456", name="source")
        self.foreign = Tenant.objects.create(code="separate-qa", name="separate")
        self.keep = NotificationLog.objects.create(tenant=self.owner, source_tenant=self.foreign, success=True)
        self.code = self.source.code
        self.seal = OpsAuditLog.objects.create(action="development.wrong-note.setup", target_tenant=self.source,
            actor_username="wrong-note-development-runner", payload=dict(schema=qa.WRONG_NOTE_SETUP_SCHEMA,
                tenant_id=self.source.pk, tenant_code=self.code,
                owner_sha256=sha256(f"{self.code}:{self.source.pk}:{self.cap}".encode()).hexdigest()))
        qa.mark_scope(self.source, "fixture", self.selected_role)
        lecture = create_lecture_fixture(tenant=self.source, title="fixture")
        req = str(uuid4())
        self.batch = EnrollmentNoticeRegistration.objects.create(tenant=self.source, lecture=lecture, actor_id=1,
            request_id=req, student_ids=[30, 31], **qa.role_choices(self.selected_role))
        self.plan = OpsAuditLog.objects.create(action=qa.PLAN, actor_username=qa.ACTOR, target_tenant=self.source,
            summary=req, payload=dict(schema=qa.SCHEMA, setup_id=self.seal.pk, tenant_id=self.source.pk,
                release="fixture", request_id=req, actor_id=1, lecture_id=lecture.pk,
                student_ids=[30, 31], redelivery_student_id=30, selected_role=self.selected_role))
        for sid in (30, 31):
            self.make_delivery(req, sid)

    def make_delivery(self, req, sid):
        target_id = f"{self.selected_role}:{sid}"
        key = sha256(target_id.encode()).hexdigest()
        excluded = "parent" if self.selected_role == "student" else "student"
        EnrollmentNoticeRecipient.objects.create(registration=self.batch, student_id=sid, role=excluded, status="skipped")
        r = EnrollmentNoticeRecipient.objects.create(registration=self.batch, student_id=sid, role=self.selected_role,
            status="admitted", business_key=key, recipient_fingerprint=key)
        box = ScheduledNotification.objects.create(tenant=self.source, trigger=f"registration_approved_{self.selected_role}",
            send_at=timezone.now(), status="sent", business_idempotency_key=key, payload={
                "domain_object_id": f"enrollment-notice:{r.pk}", "target_id": target_id,
                "source_tenant_id": self.source.pk, "tenant_id": self.owner.pk})
        r.outbox_id = box.pk
        r.save(update_fields=["outbox_id"])
        log = NotificationLog.objects.create(tenant=self.owner, source_tenant=self.source, target_type="account",
            message_mode="alimtalk", status="sent", success=True, sqs_message_id=str(uuid4()), target_id=target_id,
            provider_message_id=f"mock-{sid:012x}", recipient_fingerprint=key, business_idempotency_key=key)
        common = dict(schema=qa.SCHEMA, setup_id=self.seal.pk, plan_id=self.plan.pk, release="fixture", request_id=req,
            actor_id=1, source_tenant_id=self.source.pk, owner_tenant_id=self.owner.pk, recipient_id=r.pk, student_id=sid,
            role=self.selected_role, business_key=key, log_id=log.pk, message_id=log.sqs_message_id,
            queue="academy-v1-development-messaging-queue", receive_count=1)
        events = {"mock-send": {"provider_message_id": log.provider_message_id},
                  "ack": {"deduplicated": sid == 30, "receive_count": 2 if sid == 30 else 1}}
        if sid == 30:
            events["redelivery"] = {}
        for event, extras in events.items():
            OpsAuditLog.objects.create(action=qa.PREFIX + event, actor_username="MESSAGING", target_tenant=self.source,
                                       summary=req, payload={**common, **extras})

    def clean(self, cap=None):
        def fixture_cleanup(*args):
            Tenant.objects.filter(pk=self.source.pk, code=self.code).delete()
            return {"tenants": 0, "users": 0}
        redis = Mock()
        redis.ping.return_value = True
        redis.exists.return_value = 0
        with patch.dict(os.environ, {"ACADEMY_DEVELOPMENT_RELEASE_ID": "fixture"}), patch.object(qa, "check_mock"), patch("libs.redis.client.get_redis_client", return_value=redis), patch(
                "apps.core.management.commands.verify_development_wrong_notes.cleanup", side_effect=fixture_cleanup):
            return qa.cleanup(self.code, cap or self.cap)

    def test_committed_cleanup_and_repeat_inspect_preserve_other_sources_owner_log(self):
        self.assertFalse(any(self.clean().values()))
        self.assertFalse(any(self.clean().values()))
        self.assertEqual(list(NotificationLog.objects.values_list("pk", flat=True)), [self.keep.pk])
        self.assertTrue(Tenant.objects.filter(pk=self.owner.pk).exists())

    def test_periodic_tick_accepts_only_the_sealed_role_and_its_outboxes(self):
        with patch.object(qa, "check_mock"), patch("apps.core.management.commands.verify_development_wrong_notes.check_boundary"), patch(
                "socket.socket.connect", side_effect=AssertionError("Unexpected network connection")):
            qa.run_scheduler(self.plan.pk, "fixture", "fixture_prod")
            self.plan.payload["selected_role"] = "student" if self.selected_role == "parent" else "parent"
            self.plan.save(update_fields=["payload"])
            with self.assertRaisesRegex(CommandError, "scheduler scope"):
                qa.run_scheduler(self.plan.pk, "fixture", "fixture_prod")

    def test_wrong_owner_and_unsettled_ack_preserve_all_data(self):
        with self.assertRaises(PermissionError):
            self.clean("b" * 64)
        log = NotificationLog.objects.get(source_tenant=self.source, target_id=f"{self.selected_role}:30")
        log.tenant = self.foreign
        log.save(update_fields=["tenant"])
        with self.assertRaises(CommandError):
            self.clean()
        log.tenant = self.owner
        log.save(update_fields=["tenant"])
        qa.worker_events(self.plan).filter(action=qa.PREFIX + "ack", payload__student_id=30).delete()
        self.assertIsNone(qa.worker_evidence(self.plan, self.batch))
        with self.assertRaises(CommandError):
            self.clean()
        self.assertEqual(NotificationLog.objects.count(), 3)

    def test_repeated_mock_send_or_ack_from_first_delivery_cannot_pass(self):
        event = qa.worker_events(self.plan).get(action=qa.PREFIX + "mock-send", payload__student_id=30)
        event.pk = None
        event.save()
        with self.assertRaisesRegex(CommandError, "Duplicate"):
            qa.worker_evidence(self.plan, self.batch)
        event.delete()
        ack = qa.worker_events(self.plan).get(action=qa.PREFIX + "ack", payload__student_id=30)
        ack.payload["receive_count"] = 1
        ack.save()
        with self.assertRaisesRegex(CommandError, "receive count"):
            qa.worker_evidence(self.plan, self.batch)

    def test_preadmission_failure_is_cleanable_without_touching_other_owner_records(self):
        qa.worker_events(self.plan).delete()
        NotificationLog.objects.filter(source_tenant=self.source).delete()
        ScheduledNotification.objects.filter(tenant=self.source).delete()
        self.batch.recipients.filter(role=self.selected_role).update(status="failed", business_key="", outbox_id=None)
        self.assertFalse(any(self.clean().values()))
        self.assertEqual(list(NotificationLog.objects.values_list("pk", flat=True)), [self.keep.pk])

    def test_wrong_scenario_capability_or_actor_cannot_select_the_probe_batch(self):
        self.plan.payload["actor_id"] = 99
        with self.assertRaises(EnrollmentNoticeRegistration.DoesNotExist):
            qa.batch_for(self.plan)
        OpsAuditLog.objects.filter(action=qa.PREFIX + "scope", target_tenant=self.source).delete()
        with self.assertRaises(OpsAuditLog.DoesNotExist):
            self.clean()
        self.assertTrue(Tenant.objects.filter(pk=self.source.pk).exists())

    def test_opposite_role_or_unselected_recipient_activity_blocks_cleanup(self):
        opposite = "parent" if self.selected_role == "student" else "student"
        self.plan.payload["selected_role"] = opposite
        self.plan.save(update_fields=["payload"])
        with self.assertRaisesRegex(CommandError, "Cleanup role"):
            self.clean()
        self.plan.payload["selected_role"] = self.selected_role
        self.plan.save(update_fields=["payload"])
        self.batch.recipients.filter(role=opposite).update(status="queued")
        with self.assertRaisesRegex(CommandError, "Excluded role"):
            self.clean()
        self.assertEqual(NotificationLog.objects.count(), 3)


class ParentNoticeDevelopmentTests(EnrollmentNoticeDevelopmentTests):
    selected_role = "parent"


class EnrollmentFamilyFixtureTests(TransactionTestCase):
    def test_fixed_setup_links_both_usable_contacts_and_deletes_only_its_accounts(self):
        from apps.core.management.commands.verify_development_wrong_notes import setup
        from apps.domains.students.models import Student
        from apps.support.enrollment.notice_contacts import registration_notice_contact

        code = "qa-ymath-realuse-wn-1-1-abcd1234abcd"
        with patch("socket.socket.connect", side_effect=AssertionError("Unexpected network connection")):
            tenant = setup(code, "a" * 64, "synthetic-family-password")
            roster = Student.objects.filter(tenant=tenant).order_by("ps_number")
            self.assertEqual(roster.filter(parent=None).count(), 2)
            with transaction.atomic():
                with self.assertRaisesRegex(CommandError, "owned enrollment fixture"):
                    qa.prepare_families(tenant, "synthetic-family-password")
                qa.mark_scope(tenant, "synthetic-release", "parent")
                qa.prepare_families(tenant, "synthetic-family-password")
                qa.prepare_families(tenant, "synthetic-family-password")
            account_ids = set()
            for i, child in enumerate(roster.select_related("parent__user"), 1):
                self.assertEqual(registration_notice_contact(child, "student"), (f"01098{i:06d}", child.user_id))
                self.assertEqual(registration_notice_contact(child, "parent"), (f"01099{i:06d}", child.parent.user_id))
                account_ids.update((child.user_id, child.parent.user_id))
            self.assertEqual(len(account_ids), 4)
            call_command("setup_ymath_realuse_scenario", tenant_code=code, destroy=True, stdout=StringIO())
            self.assertFalse(get_user_model().objects.filter(pk__in=account_ids).exists())
