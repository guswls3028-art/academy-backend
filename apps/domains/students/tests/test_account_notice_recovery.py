from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.test_support import create_enrollment_fixture
from apps.domains.lectures.test_support import create_lecture_fixture
from apps.domains.messaging.test_support import (
    admit_registration_notification_fixture,
    create_scheduled_notification_fixture,
    scheduled_notification_fixtures,
)
from apps.domains.students.services.account_notice import (
    dispatch_pending_account_notice,
    initial_account_notice_state,
)
from apps.domains.students.test_support import create_student_account_fixture
from apps.domains.students.views.account_notification_views import StudentAccountNotificationLogView


class AccountNoticeRecoveryTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="notice-recovery", name="계정안내 검증", account_password_policy={"parent_mode": "phone_last4"})
        self.student = create_student_account_fixture(
            tenant=self.tenant, password="existing-password",
            student_data={"ps_number": "REC001", "omr_code": "34567890", "name": "검증 학생",
                          "phone": "01098765432", "parent_phone": "01012345678"},
        )
        self.lecture = create_lecture_fixture(tenant=self.tenant, title="수강", name="수강", subject="MATH")
        self.teacher = get_user_model().objects.create_user(
            username="notice-teacher", tenant=self.tenant, password="teacher-password",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.teacher, role="teacher")
        self.factory = APIRequestFactory()

    def enroll(self):
        return create_enrollment_fixture(tenant=self.tenant, student=self.student, lecture=self.lecture, status="ACTIVE")

    def request(self, method="get", student_id=None, data=None):
        request = getattr(self.factory, method)("/account-notifications/", data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.teacher)
        return StudentAccountNotificationLogView.as_view()(request, student_id=student_id or self.student.id)

    def produce(self, **kwargs):
        count = 0
        for target in ("student", "parent"):
            count += admit_registration_notification_fixture(
                business_tenant_id=self.tenant.id,
                trigger=f"registration_approved_{target}",
                first_enrollment_since=kwargs["first_enrollment_since"],
                tenant_id=self.tenant.id,
                source_tenant_id=self.tenant.id,
                target_type="account", target_id=f"{target}:{self.student.id}",
                event_type=f"registration_approved_{target}",
                to=self.student.phone if target == "student" else self.student.parent_phone,
                text="synthetic", message_mode="alimtalk", template_id="approved-test",
            )
        return {"status": "enqueued", "enqueued": count}

    def test_unenrolled_pending_student_has_explanation_and_cannot_send(self):
        self.assertEqual(self.request().data["initial_notice"]["status"], "awaiting_enrollment")
        with patch("apps.support.students.account_notice_dependencies.send_welcome_messages") as send:
            response = self.request("post", data={"target": "initial"})
        self.assertEqual(response.status_code, 409)
        send.assert_not_called()
        self.assertFalse(scheduled_notification_fixtures().exists())

    def test_active_pending_retry_persists_pair_and_repeat_does_not_send(self):
        self.enroll()
        self.assertEqual(self.request().data["initial_notice"]["status"], "action_required")
        old_hash = self.student.user.password
        with patch("apps.support.students.account_notice_dependencies.send_welcome_messages", side_effect=self.produce):
            response = self.request("post", data={"target": "initial"})
            repeat = self.request("post", data={"target": "initial"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("발송을 요청", response.data["message"])
        self.assertEqual(repeat.status_code, 409)
        self.assertEqual(scheduled_notification_fixtures().count(), 2)
        self.student.refresh_from_db()
        self.student.user.refresh_from_db()
        self.assertEqual(self.student.user.password, old_hash)
        self.assertFalse(self.student.pending_account_notice_student_password_ciphertext)
        self.assertEqual(self.request().data["initial_notice"]["status"], "not_pending")

    def test_partial_pair_rolls_back_new_outbox_and_enqueue_callbacks(self):
        self.enroll()
        def partial(**kwargs):
            self.produce(**kwargs)
            return {"status": "enqueued", "enqueued": 1}
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with patch("apps.support.students.account_notice_dependencies.send_welcome_messages", side_effect=partial):
                response = self.request("post", data={"target": "initial"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(callbacks, [])
        self.assertFalse(scheduled_notification_fixtures().exists())
        self.student.refresh_from_db()
        self.assertTrue(self.student.pending_account_notice_student_password_ciphertext)
        self.assertEqual(initial_account_notice_state(self.student)["status"], "action_required")

    def test_historical_partial_admission_is_reused_without_duplicate(self):
        self.enroll()
        first = create_scheduled_notification_fixture(
            tenant=self.tenant, trigger="registration_approved_student",
            send_at=self.student.pending_account_notice_since, status="sent",
            payload={"redacted": True, "target_id": f"student:{self.student.id}"},
        )
        with patch("apps.support.students.account_notice_dependencies.send_welcome_messages", side_effect=self.produce):
            result = dispatch_pending_account_notice(student_id=self.student.id)
        self.assertEqual(result["status"], "enqueued")
        self.assertEqual(scheduled_notification_fixtures().count(), 2)
        first.refresh_from_db()
        self.assertEqual(first.status, "sent")

    def test_foreign_student_is_unavailable_for_history_and_retry(self):
        other = Tenant.objects.create(code="other-notice", name="다른 학원", account_password_policy={"parent_mode": "phone_last4"})
        # Use a real foreign identity without changing the protected owner of a saved Student.
        foreign = create_student_account_fixture(
            tenant=other, password="other-password",
            student_data={"ps_number": "FOREIGN", "omr_code": "12345678", "name": "다른 학생",
                          "phone": "01088887777", "parent_phone": "01066665555"},
        )
        with patch("apps.support.students.account_notice_dependencies.send_welcome_messages") as send:
            self.assertEqual(self.request(student_id=foreign.id).status_code, 404)
            self.assertEqual(self.request("post", student_id=foreign.id, data={"target": "initial"}).status_code, 404)
        send.assert_not_called()
