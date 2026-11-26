from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment, EnrollmentNoticeRecipient, EnrollmentNoticeRegistration
from apps.domains.lectures.test_support import create_lecture_fixture
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, NotificationLog, ScheduledNotification
from apps.domains.messaging.sqs_queue import MessagingSQSQueue
from apps.domains.messaging.test_support import admit_registration_notification_fixture
from apps.domains.students.test_support import create_student_account_fixture
from apps.support.enrollment.notice_dispatch import process_registration_notices
from apps.support.messaging.student_dependencies import assert_current_delivery_contact


class RegistrationNoticesTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="registration-notice-test", name="수강", messaging_is_active=True,
                                           account_password_policy={"parent_mode": "phone_last4"})
        self.student = create_student_account_fixture(
            tenant=self.tenant, password="test-password",
            student_data={"ps_number": "NOTICE001", "omr_code": "34567890", "name": "테스트",
                          "phone": "01098765432", "parent_phone": "01012345678"},
        )
        self.lecture = create_lecture_fixture(tenant=self.tenant, title="수강", name="수강", subject="MATH")
        self.actor = self.make_actor("teacher")
        self.client.force_authenticate(self.actor)
        self.headers = {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant.code}
        self.data = {"lecture": self.lecture.pk, "students": [self.student.pk], "student_notice": "auto",
                     "parent_notice": "auto", "request_id": str(uuid4())}

    def make_actor(self, role):
        user = get_user_model().objects.create_user(username="notice-" + role, tenant=self.tenant, password="test")
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
        return user

    def post(self, **changes):
        return self.client.post("/api/v1/enrollments/register-with-notices/", {**self.data, **changes},
                                format="json", **self.headers)

    def latest(self):
        return self.client.get("/api/v1/enrollments/registration-notices/", {"lecture": self.lecture.pk}, **self.headers)

    def test_first_registration_replay_skip_and_deliberate_duplicate(self):
        row = self.post(parent_notice="skip")
        self.assertEqual(row.status_code, 201, row.data)
        self.assertEqual(row.data["students"][0]["student"], {"status": "queued", "log_id": None})
        self.assertEqual(row.data["students"][0]["parent"]["status"], "skipped")
        self.assertEqual(self.post(parent_notice="skip").data, row.data)
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(self.latest().data, row.data)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.pending_account_notice_since)
        self.assertEqual(self.post(request_id=str(uuid4())).data["students"][0]["student"]["status"], "not_first")
        repeat = self.post(request_id=str(uuid4()), student_notice="send", parent_notice="skip")
        self.assertEqual(repeat.data["students"][0]["student"]["status"], "queued")
        self.assertEqual(Enrollment.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(EnrollmentNoticeRecipient.objects.filter(status="queued").count(), 2)

    def test_shared_contact_and_explicit_one_role(self):
        self.student.phone = self.student.parent_phone
        self.student.save(update_fields=["phone"])
        first = self.post().data["students"][0]
        self.assertEqual((first["student"]["status"], first["parent"]["status"]), ("shared_recipient", "queued"))
        second = self.post(request_id=str(uuid4()), student_notice="send", parent_notice="skip")
        self.assertEqual(second.data["students"][0]["student"]["status"], "queued")

    def test_invalid_or_identifier_contact_admits_only_the_other_role(self):
        parent = self.student.parent
        invalid_contacts = [
            ("student", "identifier", self.student.phone),
            ("student", "missing", None),
            ("parent", "missing", ""),
        ]
        for role in ("student", "parent"):
            invalid_contacts.extend([
                (role, "placeholder", "01000000000"),
                (role, "letter", "0101234x5678"),
                (role, "too long", "010123456789"),
            ])

        for role, reason, value in invalid_contacts:
            with self.subTest(role=role, reason=reason), transaction.atomic():
                if role == "student":
                    type(self.student).objects.filter(pk=self.student.pk).update(
                        phone=value, uses_identifier=reason == "identifier",
                    )
                    other, expected_phone = "parent", self.student.parent_phone
                else:
                    type(self.student).objects.filter(pk=self.student.pk).update(parent_phone=value)
                    type(parent).objects.filter(pk=parent.pk).update(phone=value)
                    other, expected_phone = "student", self.student.phone

                response = self.post()
                self.assertEqual(response.status_code, 201, response.data)
                item = response.data["students"][0]
                self.assertEqual(item[role], {"status": "no_recipient", "log_id": None})
                self.assertEqual(item[other], {"status": "queued", "log_id": None})
                with patch("apps.support.enrollment.notice_dispatch.send_welcome_messages", side_effect=self.emit_fixture) as send:
                    process_registration_notices(limit=2)
                send.assert_called_once()
                self.assertEqual(send.call_args.kwargs["recipient_roles"], (other,))
                outbox = ScheduledNotification.objects.get(tenant=self.tenant)
                self.assertEqual(outbox.payload["to"], expected_phone)
                self.assertEqual(EnrollmentNoticeRecipient.objects.get(role=other).outbox_id, outbox.pk)
                self.assertIsNone(EnrollmentNoticeRecipient.objects.get(role=role).outbox_id)
                # Each matrix entry starts with the same unconsumed first-enrollment fixture.
                transaction.set_rollback(True)

    def test_batch_scheduler_handles_new_and_existing_students_without_another_request(self):
        second = create_student_account_fixture(
            tenant=self.tenant, password="test-password",
            student_data={"ps_number": "NOTICE002", "omr_code": "56789012", "name": "둘째",
                          "phone": "01088887777", "parent_phone": "01077776666"},
        )
        self.assertEqual(self.post(student_notice="skip", parent_notice="skip").status_code, 201)
        batch = self.post(students=[self.student.pk, second.pk], request_id=str(uuid4()),
                          student_notice="auto", parent_notice="send")
        self.assertEqual(batch.status_code, 201, batch.data)
        self.assertEqual([r["student"]["status"] for r in batch.data["students"]], ["not_first", "queued"])
        with patch("apps.support.enrollment.notice_dispatch.send_welcome_messages", side_effect=self.emit_fixture):
            self.assertEqual(process_registration_notices(limit=1), 0)
            self.assertEqual(ScheduledNotification.objects.count(), 1)
            self.assertEqual(process_registration_notices(limit=5), 0)
            self.assertEqual(process_registration_notices(limit=5), 0)
        self.assertEqual(ScheduledNotification.objects.count(), 3)
        self.assertEqual(Enrollment.objects.filter(tenant=self.tenant, lecture=self.lecture).count(), 2)

    def test_real_templates_and_original_recipient_bindings_reach_durable_outboxes(self):
        # Legacy/imported roster rows can retain formatting while the account uses digits.
        type(self.student).objects.filter(pk=self.student.pk).update(
            phone="010 9876-5432", parent_phone="010-1234-5678",
        )
        for role in ("student", "parent"):
            template = MessageTemplate.objects.create(
                tenant=self.tenant, name="등록 " + role, body="#{학생이름} #{학생아이디} #{사이트링크}",
                solapi_status="APPROVED", solapi_template_id="approved-fixture-" + role,
            )
            AutoSendConfig.objects.update_or_create(
                tenant=self.tenant, trigger="registration_approved_" + role, defaults={"template": template},
            )
        with patch("apps.domains.messaging.policy.get_owner_tenant_id", return_value=self.tenant.pk), \
             patch("apps.domains.messaging.scheduled.process_due_notifications") as handoff, \
             self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post().status_code, 201)
        self.assertEqual(handoff.call_count, 2)
        outboxes = ScheduledNotification.objects.filter(tenant=self.tenant)
        for role, phone in (("student", self.student.phone), ("parent", self.student.parent_phone)):
            recipient = EnrollmentNoticeRecipient.objects.get(role=role)
            self.assertEqual(recipient.status, "admitted")
            outbox = outboxes.get(pk=recipient.outbox_id)
            self.assertEqual(outbox.payload["to"], phone)
            self.assertEqual(outbox.payload["target_id"], f"{role}:{self.student.pk}")
            self.assertEqual(outbox.payload["template_id"], "approved-fixture-" + role)
            self.assertEqual(outbox.business_idempotency_key, recipient.business_key)
            self.assertEqual(outbox.payload["domain_object_id"], f"enrollment-notice:{recipient.pk}")
            job = self.worker_job(outbox)
            self.assertEqual(job["business_idempotency_key"], recipient.business_key)
            assert_current_delivery_contact(job, business_tenant_id=self.tenant.pk)
        self.assertEqual(process_registration_notices(limit=10), 0)
        self.assertEqual(outboxes.count(), 2)

    def worker_job(self, outbox):
        # Exercise the actual on-wire metadata while replacing only the external queue.
        payload = dict(outbox.payload)
        payload.pop("recipient_fingerprint")
        with patch("apps.domains.messaging.sqs_queue.get_queue_client") as client:
            queue = MessagingSQSQueue(wake_messaging_workers=False)
            self.assertTrue(queue.enqueue(**payload))
            return client.return_value.send_message.call_args.kwargs["message"]

    def test_admitted_worker_jobs_require_the_exact_live_recipient_and_active_enrollment(self):
        self.assertEqual(self.post().status_code, 201)
        with patch("apps.domains.messaging.policy.get_owner_tenant_id", return_value=self.tenant.pk), \
             patch("apps.support.enrollment.notice_dispatch.send_welcome_messages", side_effect=self.emit_fixture):
            process_registration_notices(limit=2)
        recipients = {row.role: row for row in EnrollmentNoticeRecipient.objects.all()}
        jobs = {role: self.worker_job(ScheduledNotification.objects.get(pk=row.outbox_id))
                for role, row in recipients.items()}
        for job in jobs.values():
            assert_current_delivery_contact(job, business_tenant_id=self.tenant.pk)
        student_job, parent_job = jobs["student"], jobs["parent"]

        def rejected(job, tenant_id=self.tenant.pk):
            with self.assertRaisesRegex(ValueError, "recipient_contact_changed"):
                assert_current_delivery_contact(job, business_tenant_id=tenant_id)

        foreign = Tenant.objects.create(code="worker-foreign-receipt", name="other")
        rejected(student_job, tenant_id=foreign.pk)
        for changes in (
            {"to": self.student.parent_phone},
            {"recipient_fingerprint": parent_job["recipient_fingerprint"]},
            {"domain_object_id": parent_job["domain_object_id"]},
            {"business_idempotency_key": parent_job["business_idempotency_key"]},
            {"target_type": "student"}, {"target_id": parent_job["target_id"]},
            {"event_type": parent_job["event_type"]},
        ):
            with self.subTest(fields=tuple(changes)):
                rejected({**student_job, **changes})

        # A legacy account job has no receipt and retains its existing dispatch path.
        legacy = dict(student_job)
        legacy.pop("domain_object_id")
        with self.assertNumQueries(0):
            assert_current_delivery_contact(legacy, business_tenant_id=self.tenant.pk)

        student = type(self.student).objects.filter(pk=self.student.pk)
        parent = type(self.student.parent).objects.filter(pk=self.student.parent_id)
        users = get_user_model().objects
        cases = (
            (student, {"uses_identifier": True}, {"student"}),
            (student, {"phone": "01099998888"}, {"student"}),
            (parent, {"phone": "01099998888"}, {"parent"}),
            (parent, {"user_id": self.make_actor("parent").pk}, {"parent"}),
            (users.filter(pk=self.student.user_id), {"is_active": False}, {"student", "parent"}),
            (users.filter(pk=self.student.parent.user_id), {"is_active": False}, {"parent"}),
            (EnrollmentNoticeRecipient.objects.filter(pk=recipients["student"].pk),
             {"recipient_fingerprint": recipients["parent"].recipient_fingerprint}, {"student"}),
            (Enrollment.objects.filter(tenant=self.tenant, lecture=self.lecture, student=self.student),
             {"status": "INACTIVE"}, {"student", "parent"}),
        )
        for rows, changes, blocked_roles in cases:
            with self.subTest(model=rows.model, fields=tuple(changes), roles=blocked_roles), transaction.atomic():
                self.assertEqual(rows.update(**changes), 1)
                for role, job in jobs.items():
                    if role in blocked_roles:
                        rejected(job)
                    else:
                        assert_current_delivery_contact(job, business_tenant_id=self.tenant.pk)
                transaction.set_rollback(True)

    def test_historical_first_enrollment_outbox_is_reused_without_retroactive_recipient_marker(self):
        admit_registration_notification_fixture(
            business_tenant_id=self.tenant.pk, trigger="registration_approved_student",
            tenant_id=self.tenant.pk, source_tenant_id=self.tenant.pk, target_type="account",
            target_id=f"student:{self.student.pk}", event_type="registration_approved_student",
            text="synthetic", template_id="approved-test", to=self.student.phone, message_mode="alimtalk",
        )
        original = ScheduledNotification.objects.get(tenant=self.tenant)
        self.assertNotIn("domain_object_id", original.payload)
        self.assertEqual(self.post(parent_notice="skip").status_code, 201)
        with patch("apps.support.enrollment.notice_dispatch.send_welcome_messages", side_effect=self.emit_fixture):
            process_registration_notices(limit=2)
        receipt = EnrollmentNoticeRecipient.objects.get(role="student")
        self.assertEqual((receipt.status, receipt.outbox_id), ("admitted", original.pk))
        self.assertEqual(ScheduledNotification.objects.count(), 1)
        original.refresh_from_db()
        self.assertNotIn("domain_object_id", original.payload)

    def test_invalid_foreign_or_staff_override_cannot_write_or_read_another_actor(self):
        self.assertEqual(self.latest().status_code, 204)
        self.assertEqual(self.post(student_notice="unknown").status_code, 400)
        other = Tenant.objects.create(code="other-registration-notice", name="other")
        foreign_lecture = create_lecture_fixture(tenant=other, title="외부", name="외부", subject="MATH")
        self.assertEqual(self.post(lecture=foreign_lecture.pk).status_code, 404)
        self.assertEqual(self.post(students=[self.student.pk, 999999]).status_code, 400)
        self.assertFalse(EnrollmentNoticeRegistration.objects.exists())
        self.assertEqual(self.post().status_code, 201)
        self.client.force_authenticate(self.make_actor("staff"))
        self.assertEqual(self.latest().status_code, 204)
        self.assertEqual(self.post(student_notice="send").status_code, 403)

    def test_changed_contact_does_not_send_and_fails_observably(self):
        self.post(parent_notice="skip")
        self.student.phone = "01099998888"
        self.student.save(update_fields=["phone"])
        call_command("process_scheduled_notifications")
        self.assertEqual(self.latest().data["students"][0]["student"]["status"], "failed")
        self.assertFalse(ScheduledNotification.objects.exists())

    def test_queued_contact_invalidation_and_parent_account_relink_fail_before_dispatch(self):
        parent = self.student.parent
        replacement = self.make_actor("parent")
        for role, change in (
            ("student", "identifier"),
            ("student", "invalid number"),
            ("parent", "invalid account phone"),
            ("parent", "account relink"),
        ):
            with self.subTest(role=role, change=change), transaction.atomic():
                other = "parent" if role == "student" else "student"
                response = self.post(**{other + "_notice": "skip"})
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data["students"][0][role]["status"], "queued")
                if change == "identifier":
                    type(self.student).objects.filter(pk=self.student.pk).update(uses_identifier=True)
                elif change == "invalid number":
                    type(self.student).objects.filter(pk=self.student.pk).update(phone="0109876x5432")
                elif change == "invalid account phone":
                    type(parent).objects.filter(pk=parent.pk).update(phone="0101234x5678")
                else:
                    type(parent).objects.filter(pk=parent.pk).update(user_id=replacement.pk)

                with patch("apps.support.enrollment.notice_dispatch.send_welcome_messages") as send:
                    process_registration_notices(limit=2)
                send.assert_not_called()
                item = self.latest().data["students"][0]
                self.assertEqual(item[role], {"status": "failed", "log_id": None})
                self.assertEqual(item[other], {"status": "skipped", "log_id": None})
                self.assertFalse(ScheduledNotification.objects.exists())
                transaction.set_rollback(True)

    def emit_fixture(self, *, created_students, recipient_roles, admitted_outboxes, **kwargs):
        student, = created_students
        role, = recipient_roles
        admit_registration_notification_fixture(
            business_tenant_id=self.tenant.id, trigger="registration_approved_" + role, admitted_outboxes=admitted_outboxes,
            tenant_id=self.tenant.id, source_tenant_id=self.tenant.id, target_type="account", target_id=f"{role}:{student.pk}",
            event_type="registration_approved_" + role, text="synthetic", message_mode="alimtalk",
            domain_object_id=kwargs["domain_object_id"], first_enrollment_since=kwargs["first_enrollment_since"],
            template_id="approved-test", to=student.phone if role == "student" else student.parent_phone,
        )

    def test_exhausted_pre_admission_jobs_signal_operator_without_outbox(self):
        self.post()
        with patch("apps.support.enrollment.notice_dispatch.send_welcome_messages", return_value={"status": "skip"}):
            for _ in range(2):
                EnrollmentNoticeRecipient.objects.update(next_attempt_at=timezone.now())
                self.assertEqual(process_registration_notices(limit=2), 0)
            EnrollmentNoticeRecipient.objects.update(next_attempt_at=timezone.now())
            with self.assertRaisesMessage(CommandError, "failed for 2 recipient"):
                call_command("process_scheduled_notifications")
        self.assertEqual(EnrollmentNoticeRecipient.objects.filter(status="failed").count(), 2)
        self.assertFalse(ScheduledNotification.objects.exists())

    def test_scheduler_retry_correlates_only_this_registration_and_never_claims_final_delivery(self):
        self.post(parent_notice="skip")
        recipient = EnrollmentNoticeRecipient.objects.get(role="student")
        with patch("apps.support.enrollment.notice_dispatch.send_welcome_messages", side_effect=RuntimeError("pre-admission")):
            self.assertEqual(process_registration_notices(limit=2), 0)
        recipient.refresh_from_db()
        self.assertEqual((recipient.status, recipient.attempts), ("queued", 1))
        EnrollmentNoticeRecipient.objects.filter(pk=recipient.pk).update(next_attempt_at=timezone.now())
        with patch("apps.support.enrollment.notice_dispatch.send_welcome_messages", side_effect=self.emit_fixture):
            process_registration_notices(limit=2)
            process_registration_notices(limit=2)
        recipient.refresh_from_db()
        self.assertEqual(recipient.status, "admitted")
        self.assertEqual(ScheduledNotification.objects.count(), 1)
        outbox = ScheduledNotification.objects.get(pk=recipient.outbox_id)
        log = NotificationLog.objects.create(tenant=self.tenant, source_tenant=self.tenant, message_mode="alimtalk",
                    business_idempotency_key=outbox.business_idempotency_key, provider_message_id="fixture",
                    success=True, status="sent")
        self.assertEqual(self.latest().data["students"][0]["student"], {"status": "accepted", "log_id": log.pk})
        log.status = "ambiguous"
        log.save(update_fields=["status"])
        self.assertEqual(self.latest().data["students"][0]["student"]["status"], "ambiguous")
        verify = {"lecture": self.lecture.pk, "request_id": self.data["request_id"], "log_id": log.pk}
        receipt = {"status": "delivered", "status_code": "4000", "checked_at": timezone.now(),
                   "updated_at": timezone.now(), "failure_reason": ""}
        with patch("apps.domains.messaging.provider_delivery.get_provider_delivery_status", return_value=receipt) as provider:
            unrelated = self.client.post("/api/v1/enrollments/verify-registration-notice/",
                                         {**verify, "request_id": str(uuid4())}, format="json", **self.headers)
            self.assertEqual(unrelated.status_code, 404)
            provider.assert_not_called()
            resolved = self.client.post("/api/v1/enrollments/verify-registration-notice/",
                                        verify, format="json", **self.headers)
            self.assertEqual(resolved.data["provider_status_code"], "4000")
            provider.assert_called_once()
        self.assertEqual(self.latest().data["students"][0]["student"], {"status": "delivered", "log_id": log.pk})
