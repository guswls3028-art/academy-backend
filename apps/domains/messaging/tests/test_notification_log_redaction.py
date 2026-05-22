from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from academy.adapters.db.django.repositories_messaging import (
    claim_notification_slot,
    create_notification_log,
    finalize_notification,
)
from apps.core.models import Tenant
from apps.domains.messaging.models import NotificationLog
from apps.domains.messaging.security import SENSITIVE_MESSAGE_PLACEHOLDER


class NotificationLogRedactionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Redaction", code="redact", is_active=True)

    def test_sensitive_notification_type_does_not_store_message_body(self):
        created = create_notification_log(
            tenant_id=self.tenant.id,
            success=True,
            amount_deducted=Decimal("0"),
            recipient_summary="010****1234",
            template_summary="비밀번호 재설정(학생)",
            message_body="임시 비밀번호: 12345678",
            message_mode="alimtalk",
            notification_type="password_reset_student",
        )

        self.assertTrue(created)
        log = NotificationLog.objects.get()
        self.assertEqual(log.message_body, SENSITIVE_MESSAGE_PLACEHOLDER)

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
            notification_type="password_find_otp",
        )

        log = NotificationLog.objects.get(id=log_id)
        self.assertEqual(log.message_body, SENSITIVE_MESSAGE_PLACEHOLDER)

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
