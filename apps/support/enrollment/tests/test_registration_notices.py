from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment, EnrollmentNoticeRecipient, EnrollmentNoticeRegistration
from apps.domains.lectures.test_support import create_lecture_fixture
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, NotificationLog, ScheduledNotification
from apps.domains.messaging.test_support import admit_registration_notification_fixture
from apps.domains.students.test_support import create_student_account_fixture
from apps.support.enrollment.notice_dispatch import process_registration_notices


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
        type(self.student).objects.filter(pk=self.student.pk).update(parent_phone="010-1234-5678")
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
        self.assertEqual(process_registration_notices(limit=10), 0)
        self.assertEqual(outboxes.count(), 2)

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

    def emit_fixture(self, *, created_students, recipient_roles, admitted_outboxes, **kwargs):
        student, = created_students
        role, = recipient_roles
        admit_registration_notification_fixture(
            business_tenant_id=self.tenant.id, trigger="registration_approved_" + role, admitted_outboxes=admitted_outboxes,
            tenant_id=self.tenant.id, source_tenant_id=self.tenant.id, target_type="account", target_id=f"{role}:{student.pk}",
            event_type="registration_approved_" + role, text="synthetic", message_mode="alimtalk",
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
