from __future__ import annotations

from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership, WorkerHeartbeatModel
from apps.core.models.user import user_internal_username
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, NotificationLog, ScheduledNotification
from apps.domains.messaging.views.operations_views import MessagingOperationsStatusView, SendMessagePreflightView


User = get_user_model()
Student = apps.get_model("students", "Student")


class MessagingOperationsBase(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="msg-ops",
            name="Msg Ops",
            is_active=True,
            messaging_sender="01012345678",
        )
        self.owner = User.objects.create_user(
            username="msg-ops-owner",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")

    def _request(self, method: str, path: str, data=None):
        request = getattr(self.factory, method)(path, data=data or {}, format="json")
        force_authenticate(request, user=self.owner)
        request.user = self.owner
        request.tenant = self.tenant
        return request

    def _student(self, suffix: str, phone: str = "01011112222", parent_phone: str = "01033334444"):
        user = User.objects.create_user(
            username=user_internal_username(self.tenant, suffix),
            password="test1234",
            tenant=self.tenant,
            phone=phone,
            name=f"학생{suffix}",
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number=suffix,
            name=f"학생{suffix}",
            phone=phone,
            parent_phone=parent_phone,
            omr_code=f"99{suffix}",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role="student")
        return student


class SendMessagePreflightViewTests(MessagingOperationsBase):
    def test_preflight_reports_recipient_template_and_phone_health(self):
        ok_student = self._student("001", parent_phone="01033334444")
        no_phone_student = self._student("002", parent_phone="")
        deleted_student = self._student("003", parent_phone="01055556666")
        deleted_student.deleted_at = timezone.now()
        deleted_student.save(update_fields=["deleted_at"])
        MessageTemplate.objects.create(
            tenant=self.tenant,
            category="default",
            name="자유양식",
            subject="",
            body="#{공지내용}\n#{사이트링크}",
            solapi_template_id="FREEFORM-SID",
            solapi_status="APPROVED",
            is_system=True,
        )

        response = SendMessagePreflightView.as_view()(
            self._request(
                "post",
                "/api/v1/messaging/send/preflight/",
                {
                    "send_to": "parent",
                    "student_ids": [ok_student.id, no_phone_student.id, deleted_student.id, ok_student.id],
                    "raw_body": "성적표 안내입니다.",
                    "block_category": "default",
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["ok"])
        self.assertEqual(response.data["recipient"]["selected"], 3)
        self.assertEqual(response.data["recipient"]["resolved"], 2)
        self.assertEqual(response.data["recipient"]["valid_phone"], 1)
        self.assertEqual(response.data["recipient"]["skipped_no_phone"], 1)
        self.assertEqual(response.data["recipient"]["invalid_or_deleted"], 1)
        self.assertEqual(response.data["template"]["source"], "freeform")
        self.assertEqual(response.data["template"]["solapi_status"], "APPROVED")
        self.assertTrue(any(item["code"] == "missing_phone" for item in response.data["warnings"]))

    def test_preflight_blocks_without_approved_template(self):
        student = self._student("004")

        response = SendMessagePreflightView.as_view()(
            self._request(
                "post",
                "/api/v1/messaging/send/preflight/",
                {
                    "send_to": "parent",
                    "student_ids": [student.id],
                    "raw_body": "검수 전 본문입니다.",
                    "block_category": "default",
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["ok"])
        self.assertTrue(any(item["code"] == "template_not_ready" for item in response.data["blockers"]))


class MessagingOperationsStatusViewTests(MessagingOperationsBase):
    def test_operations_status_summarizes_worker_queue_logs_and_auto_send_risks(self):
        now = timezone.now()
        WorkerHeartbeatModel.objects.create(
            name="messaging",
            instance="i-stale",
            last_seen_at=now - timedelta(minutes=10),
            version="sha-old",
        )
        ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="manual_send",
            send_at=now - timedelta(minutes=5),
            status=ScheduledNotification.Status.PENDING,
            payload={"to": "01011112222", "text": "예약"},
        )
        ScheduledNotification.objects.create(
            tenant=self.tenant,
            trigger="manual_send",
            send_at=now - timedelta(minutes=1),
            status=ScheduledNotification.Status.FAILED,
            payload={"to": "01011112222", "text": "예약"},
        )
        NotificationLog.objects.create(
            tenant=self.tenant,
            success=True,
            status="sent",
            recipient_summary="학생",
            message_mode="alimtalk",
        )
        NotificationLog.objects.create(
            tenant=self.tenant,
            success=False,
            status="failed",
            recipient_summary="학생",
            failure_reason="provider error",
            message_mode="alimtalk",
        )
        AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_reminder",
            enabled=True,
            message_mode="alimtalk",
        )

        response = MessagingOperationsStatusView.as_view()(
            self._request("get", "/api/v1/messaging/operations/status/")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["worker"]["status"], "stale")
        self.assertEqual(response.data["scheduled"]["pending"], 1)
        self.assertEqual(response.data["scheduled"]["overdue"], 1)
        self.assertEqual(response.data["scheduled"]["failed_24h"], 1)
        self.assertEqual(response.data["log_24h"]["sent"], 1)
        self.assertEqual(response.data["log_24h"]["failed"], 1)
        self.assertEqual(response.data["auto_send"]["enabled_without_template"], 0)
        self.assertTrue(any(item["code"] == "scheduled_overdue" for item in response.data["risks"]))

    def test_operations_status_marks_stale_heartbeat_idle_when_no_backlog(self):
        WorkerHeartbeatModel.objects.create(
            name="messaging",
            instance="i-old",
            last_seen_at=timezone.now() - timedelta(minutes=10),
            version="sha-old",
        )

        response = MessagingOperationsStatusView.as_view()(
            self._request("get", "/api/v1/messaging/operations/status/")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["worker"]["status"], "idle")
        self.assertEqual(response.data["worker"]["idle_reason"], "scale_to_zero_no_backlog")
        self.assertFalse(any(item["code"] == "worker_attention" for item in response.data["risks"]))
