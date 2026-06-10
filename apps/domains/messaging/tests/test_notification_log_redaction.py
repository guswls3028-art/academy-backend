from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from academy.adapters.db.django.repositories_messaging import (
    claim_notification_slot,
    create_notification_log,
    finalize_notification,
)
from apps.core.models import Tenant, TenantMembership
from apps.domains.messaging.models import NotificationLog
from apps.domains.messaging.security import SENSITIVE_MESSAGE_PLACEHOLDER
from apps.domains.messaging.views.log_views import NotificationLogDetailView, NotificationLogListView


User = get_user_model()


class NotificationLogRedactionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Redaction", code="redact", is_active=True)
        self.factory = APIRequestFactory()
        self.admin = User.objects.create_user(
            username="redact-owner",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="owner")

    def test_sensitive_notification_type_does_not_store_message_body(self):
        created = create_notification_log(
            tenant_id=self.tenant.id,
            success=True,
            amount_deducted=Decimal("0"),
            recipient_summary="010****1234",
            template_summary="비밀번호 재설정(학생)",
            message_body="임시 비밀번호: 12345678",
            message_mode="alimtalk",
            provider_message_id="group-create-1",
            notification_type="password_reset_student",
        )

        self.assertTrue(created)
        log = NotificationLog.objects.get()
        self.assertEqual(log.message_body, SENSITIVE_MESSAGE_PLACEHOLDER)
        self.assertEqual(log.provider_message_id, "group-create-1")

    def test_finalize_redacts_sensitive_body_on_claimed_log(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="redact-finalize",
            sqs_message_id="sqs-redact",
        )
        self.assertTrue(claimed)

        finalize_notification(
            log_id,
            success=True,
            message_body="인증번호: 112233",
            provider_message_id="group-finalize-1",
            notification_type="password_find_otp",
        )

        log = NotificationLog.objects.get(id=log_id)
        self.assertEqual(log.message_body, SENSITIVE_MESSAGE_PLACEHOLDER)
        self.assertEqual(log.provider_message_id, "group-finalize-1")

    def test_non_sensitive_body_keeps_body_but_masks_secret_like_lines(self):
        create_notification_log(
            tenant_id=self.tenant.id,
            success=True,
            amount_deducted=Decimal("0"),
            recipient_summary="010****1234",
            template_summary="일반 안내",
            message_body="안내 본문\n비밀번호: 1234\n감사합니다",
            message_mode="alimtalk",
            notification_type="clinic_reservation_created",
        )

        log = NotificationLog.objects.get()
        self.assertIn("안내 본문", log.message_body)
        self.assertIn("비밀번호: [보안상 숨김]", log.message_body)
        self.assertNotIn("1234", log.message_body)

    def test_log_api_exposes_provider_message_id_for_provider_verification(self):
        create_notification_log(
            tenant_id=self.tenant.id,
            success=True,
            amount_deducted=Decimal("1"),
            recipient_summary="010****1234",
            template_summary="실발송 검증",
            message_body="통제번호 실발송 검증",
            message_mode="alimtalk",
            provider_message_id="group-provider-proof",
            notification_type="manual_send",
        )
        log = NotificationLog.objects.get()

        list_request = self.factory.get("/api/v1/messaging/log/")
        force_authenticate(list_request, user=self.admin)
        list_request.user = self.admin
        list_request.tenant = self.tenant
        list_response = NotificationLogListView.as_view()(list_request)

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["results"][0]["provider_message_id"], "group-provider-proof")

        detail_request = self.factory.get(f"/api/v1/messaging/log/{log.id}/")
        force_authenticate(detail_request, user=self.admin)
        detail_request.user = self.admin
        detail_request.tenant = self.tenant
        detail_response = NotificationLogDetailView.as_view()(detail_request, pk=log.id)

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["provider_message_id"], "group-provider-proof")
