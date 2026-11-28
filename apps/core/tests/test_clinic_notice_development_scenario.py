"""Local database coverage for the fixed clinic development receipt and cleanup contract."""
import os
from hashlib import sha256
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.core.management.commands.setup_ymath_realuse_scenario import DEVELOPMENT_MESSAGING_OWNER_CODE
from apps.core.models import OpsAuditLog, Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.services import clinic_notice_development_scenario as qa
from apps.domains.clinic.models import Session, SessionParticipant
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.test_support import create_lecture_fixture
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, NotificationLog, ScheduledNotification
from apps.domains.students.test_support import create_student_fixture


@override_settings(MESSAGING_TENANT_BINDING_KEY="local-clinic-test-only")
class ClinicNoticeDevelopmentTests(TransactionTestCase):
    cap = "a" * 64
    code = "qa-ymath-realuse-wn-1-1-abcdef123456"

    def setUp(self):
        self.owner = Tenant.objects.create(code=DEVELOPMENT_MESSAGING_OWNER_CODE, name="mock owner")
        self.source = Tenant.objects.create(code=self.code, name="fixture")
        self.foreign = Tenant.objects.create(code="other-qa", name="other")
        self.keep = NotificationLog.objects.create(tenant=self.owner, source_tenant=self.foreign, success=True)
        self.seal = OpsAuditLog.objects.create(action="development.wrong-note.setup", actor_username="wrong-note-development-runner",
            target_tenant=self.source, payload=dict(schema=qa.WRONG_NOTE_SETUP_SCHEMA, tenant_id=self.source.pk,
                tenant_code=self.code, owner_sha256=sha256(f"{self.code}:{self.source.pk}:{self.cap}".encode()).hexdigest()))
        overrides = override_settings(OWNER_TENANT_ID=self.owner.pk)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.cache = Mock()
        self.cache.exists.return_value = 0
        for guard in (patch.dict(os.environ, {"ACADEMY_DEVELOPMENT_RELEASE_ID": "fixture"}),
                      patch.object(qa, "check_mock"), patch("libs.redis.client.get_redis_client", return_value=self.cache),
                      patch("apps.core.management.commands.verify_development_wrong_notes.cleanup", side_effect=self.destroy)):
            guard.start()
            self.addCleanup(guard.stop)
        self.actor = self.user("ymath-qa-teacher", "admin")
        user = self.user("ymath-qa-student-01", "student")
        self.student = create_student_fixture(tenant=self.source, user=user, name="QA 01", ps_number="QA-0001",
            omr_code="98000001", phone=qa.PHONES["student"], parent_phone=qa.PHONES["parent"])
        self.lecture = create_lecture_fixture(tenant=self.source, title="공통수학2 정규반", name="QA lecture")
        Enrollment.objects.create(tenant=self.source, student=self.student, lecture=self.lecture, status="ACTIVE")
        template = MessageTemplate.objects.create(tenant=self.source, body=qa.MEMO, name="Clinic")
        AutoSendConfig.objects.create(tenant=self.source, trigger=qa.TRIGGER, template=template, message_mode="alimtalk",
                                     enabled=True, delay_mode="immediate")
        qa.mark_scope(self.source, "fixture")
        self.scope = OpsAuditLog.objects.get(action=qa.PREFIX + "scope")

    def user(self, name, role):
        user = get_user_model().objects.create_user(tenant=self.source, username=user_internal_username(self.source, name),
                                                    is_active=True, is_staff=role == "admin")
        TenantMembership.objects.create(tenant=self.source, user=user, role=role, is_active=True)
        return user

    def destroy(self, code, capability):
        self.assertEqual((code, capability), (self.code, self.cap))
        Tenant.objects.filter(pk=self.source.pk, code=self.code).delete()
        return {"tenants": 0, "users": 0}

    def reserve(self, status="pending"):
        fields = qa._session_fields(self.scope)
        fields.pop("target_lecture_ids")
        self.session = Session.objects.create(tenant=self.source, created_by=self.actor, **fields)
        self.session.target_lectures.add(self.lecture)
        self.plan = OpsAuditLog.objects.create(action=qa.PLAN, actor_username=qa.ACTOR, target_tenant=self.source,
            summary=self.scope.summary, payload=qa._plan_fields(self.scope, self.session))
        self.part = SessionParticipant.objects.create(tenant=self.source, session=self.session, student=self.student,
            source="student_request", status=status, booking_start_time="14:30", booking_end_time="15:30",
            requested_after_cutoff=True, status_changed_by=self.actor if status == "booked" else None)

    def persist_delivery(self, phase, role):
        target = self.actor.pk if role == "clinic_teacher" else self.student.pk
        origin = f"clinic_participant:{self.part.pk}:{phase}"
        fp = qa.build_recipient_fingerprint(qa.PHONES[role])
        payload = dict(tenant_id=self.owner.pk, source_tenant_id=self.source.pk, to=qa.PHONES[role], target_type=role,
                       target_id=target, event_type=qa.TRIGGER, message_mode="alimtalk", recipient_fingerprint=fp,
                       source_use_case=f"clinic.{qa.TRIGGER}", domain_object_id=origin, origin_id=origin,
                       occurrence_key=f"{qa.TRIGGER}:{origin}", template_id=qa.get_solapi_template_id(qa.TRIGGER))
        key = qa.build_enqueued_business_key(tenant_id=self.source.pk, payload=payload)
        box = ScheduledNotification.objects.create(tenant=self.source, trigger=qa.TRIGGER, origin_id=origin, status="sent",
            origin_type=payload["source_use_case"], payload=qa.redact_terminal_delivery_payload(trigger=qa.TRIGGER, payload=payload),
            send_at=timezone.now(), recipient_fingerprint=fp,
            business_idempotency_key=key)
        memo_state = "마감 후 승인 대기" if phase == qa.PENDING else "예약 확정"
        log = NotificationLog.objects.create(tenant=self.owner, source_tenant=self.source, notification_type=qa.TRIGGER,
            origin_type=box.origin_type, origin_id=origin, success=True, status="sent", business_idempotency_key=key,
            sqs_message_id=str(uuid4()), recipient_fingerprint=fp, target_type=role, target_id=target, message_mode="alimtalk",
            provider_message_id=f"mock-{box.pk:012x}", message_body=f"{memo_state} · {self.scope.payload['date']} 14:30–15:30")
        forced = phase == qa.PENDING and role == "clinic_teacher"
        binding = dict(schema=qa.SCHEMA, setup_id=self.seal.pk, plan_id=self.plan.pk, release="fixture",
            request_id=self.scope.summary, actor_id=self.actor.pk, source_tenant_id=self.source.pk, owner_tenant_id=self.owner.pk,
            session_id=self.session.pk, student_id=self.student.pk, teacher_id=self.actor.pk, participant_id=self.part.pk,
            origin_id=origin, role=role, outbox_id=box.pk, log_id=log.pk, business_key=key, queue=qa.QUEUE,
            message_id=log.sqs_message_id, receive_count=1)
        effects = {"mock-send": dict(provider_message_id=log.provider_message_id),
                   "ack": dict(deduplicated=forced, receive_count=2 if forced else 1)}
        if forced:
            effects["redelivery"] = {}
        for event, extra in effects.items():
            OpsAuditLog.objects.create(action=qa.PREFIX + event, target_tenant=self.source, summary=self.scope.summary,
                                      actor_username="MESSAGING", payload={**binding, **extra})
        return log

    def persist_phase(self, phase):
        return [self.persist_delivery(phase, role) for role in qa.ROLES[phase]]

    def clean(self):
        return qa.cleanup(self.code, self.cap)

    def test_five_receipts_then_cleanup_after_commit_and_redis_failure_recovery(self):
        self.reserve("booked")
        self.persist_phase(qa.PENDING)
        self.persist_phase(qa.APPROVAL)
        evidence = qa.worker_evidence(self.scope, self.plan, self.part)
        self.assertEqual({(r["phase"], r["role"]) for r in evidence["deliveries"]},
                         {(p, r) for p, roles in qa.ROLES.items() for r in roles})
        forced, = [r for r in evidence["deliveries"] if r["deduplicated"]]
        self.assertEqual((forced["phase"], forced["role"], forced["receive_count"]), (qa.PENDING, "clinic_teacher", 2))
        self.cache.delete.side_effect = [ConnectionError("cache readback interrupted"), 0, 0]
        with self.assertRaises(ConnectionError):
            self.clean()
        self.assertFalse(Tenant.objects.filter(pk=self.source.pk).exists())
        self.assertFalse(any(self.clean().values()))
        self.assertFalse(any(self.clean().values()))
        self.assertEqual(list(NotificationLog.objects.values_list("id", flat=True)), [self.keep.pk])
        self.cache.delete.assert_called_with(*(f"tenant:{self.owner.pk}:job:messaging:{msg}:progress" for msg in evidence["messages"]))

    def test_clean_settled_pending_without_approval(self):
        self.reserve()
        logs = self.persist_phase(qa.PENDING)
        self.assertEqual(set(qa.worker_evidence(self.scope, self.plan, self.part)["logs"]), {r.pk for r in logs})
        self.assertFalse(any(self.clean().values()))
        self.assertTrue(NotificationLog.objects.filter(pk=self.keep.pk).exists())

    def test_no_work_scope_and_wrong_capability(self):
        with self.assertRaises(PermissionError):
            qa.cleanup(self.code, "b" * 64)
        self.assertTrue(Tenant.objects.filter(pk=self.source.pk).exists())
        self.assertFalse(any(self.clean().values()))
        self.cache.delete.assert_not_called()

    def test_missing_ack_then_active_lock_preserve_source_and_owner(self):
        self.reserve()
        self.persist_phase(qa.PENDING)
        ack = OpsAuditLog.objects.get(action=qa.PREFIX + "ack", payload__role="clinic_teacher")
        ack.delete()
        self.assertIsNone(qa.worker_evidence(self.scope, self.plan, self.part))
        with self.assertRaisesMessage(CommandError, "unsettled"):
            self.clean()
        ack.save()
        self.cache.exists.return_value = 1
        with self.assertRaisesMessage(CommandError, "worker lock"):
            self.clean()
        self.assertEqual(NotificationLog.objects.filter(source_tenant=self.source).count(), 3)
        self.assertTrue(SessionParticipant.objects.filter(pk=self.part.pk).exists())
        self.cache.delete.assert_not_called()

    def test_unexpected_or_duplicate_events_and_foreign_owner_receipts_block_cleanup(self):
        self.reserve()
        log, *_ = self.persist_phase(qa.PENDING)
        ack = OpsAuditLog.objects.get(action=qa.PREFIX + "ack", payload__role="parent")
        ack.pk = None
        ack.save()
        with self.assertRaisesMessage(CommandError, "Duplicate or unexpected"):
            self.clean()
        ack.delete()
        NotificationLog.objects.filter(pk=log.pk).update(source_tenant=self.foreign)
        with self.assertRaisesMessage(CommandError, "log identity"):
            self.clean()
        self.assertEqual(NotificationLog.objects.filter(tenant=self.owner).count(), 4)
        self.assertTrue(Tenant.objects.filter(pk=self.source.pk).exists())

    def test_unsealed_extra_booking_or_outbox_rejected_before_cleanup(self):
        self.reserve()
        self.persist_phase(qa.PENDING)
        box = ScheduledNotification.objects.filter(tenant=self.source).first()
        box.pk = None
        box.dispatch_key = uuid4()
        box.business_idempotency_key = "e" * 64
        box.origin_id = "foreign"
        box.save()
        with self.assertRaisesMessage(CommandError, "foreign clinic outbox"):
            self.clean()
        box.delete()
        SessionParticipant.objects.create(tenant=self.source, student=self.student, source="teacher")
        with self.assertRaisesMessage(CommandError, "unbound clinic participant"):
            self.clean()
        self.assertEqual(NotificationLog.objects.filter(source_tenant=self.source).count(), 3)

    def test_foreign_session_using_synthetic_actor_is_preserved(self):
        linked = Session.objects.create(tenant=self.foreign, location="outside", start_time="10:00", max_participants=1,
                                        date=timezone.localdate(), notification_teacher=self.actor)
        with self.assertRaisesMessage(CommandError, "foreign clinic session"):
            self.clean()
        self.assertTrue(Tenant.objects.filter(pk=self.source.pk).exists())
        linked.refresh_from_db()
        self.assertEqual(linked.notification_teacher_id, self.actor.pk)

    def test_approved_without_new_receipts_and_wrong_queue_message_are_unsettled(self):
        self.reserve("booked")
        self.persist_phase(qa.PENDING)
        with self.assertRaisesMessage(CommandError, "unsettled"):
            self.clean()
        self.persist_phase(qa.APPROVAL)
        wrong = OpsAuditLog.objects.get(action=qa.PREFIX + "ack", payload__role="clinic_teacher")
        wrong.payload["message_id"] = str(uuid4())
        wrong.save(update_fields=["payload"])
        with self.assertRaisesMessage(CommandError, "receipt ownership"):
            self.clean()
        self.assertEqual(NotificationLog.objects.filter(source_tenant=self.source).count(), 5)
        self.cache.delete.assert_not_called()

    def test_changed_cleanup_snapshot_cannot_remove_another_workers_progress(self):
        self.reserve()
        self.persist_phase(qa.PENDING)
        self.assertFalse(any(self.clean().values()))
        snapshot = OpsAuditLog.objects.get(action=qa.PREFIX + "cleanup")
        snapshot.payload["messages"].append(str(uuid4()))
        snapshot.save(update_fields=["payload"])
        self.cache.reset_mock()
        with self.assertRaisesMessage(CommandError, "snapshot"):
            self.clean()
        self.cache.delete.assert_not_called()
