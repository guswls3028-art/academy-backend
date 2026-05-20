from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from academy.adapters.db.django.repositories_messaging import (
    claim_notification_slot,
    finalize_notification,
)
from apps.core.models import Tenant
from apps.domains.messaging.models import NotificationLog


class NotificationClaimReclaimTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Messaging", code="msgclaim", is_active=True)

    def test_same_sqs_message_can_reclaim_failed_business_slot(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-transient",
            sqs_message_id="sqs-1",
            recipient_summary="0101****",
        )
        self.assertTrue(claimed)
        self.assertIsNotNone(log_id)

        finalize_notification(
            log_id,
            success=False,
            failure_reason="temporary_network",
            message_body="body",
            notification_type="clinic_reservation_created",
        )

        reclaimed, reclaimed_log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-transient",
            sqs_message_id="sqs-1",
            recipient_summary="0101****",
        )

        self.assertTrue(reclaimed)
        self.assertEqual(reclaimed_log_id, log_id)
        log = NotificationLog.objects.get(id=log_id)
        self.assertEqual(log.status, "processing")
        self.assertEqual(log.failure_reason, "")

    def test_sent_business_slot_remains_duplicate(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-sent",
            sqs_message_id="sqs-2",
        )
        self.assertTrue(claimed)
        finalize_notification(log_id, success=True, amount_deducted=Decimal("10"))

        claimed_again, duplicate_log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-sent",
            sqs_message_id="sqs-2",
        )

        self.assertFalse(claimed_again)
        self.assertIsNone(duplicate_log_id)

    def test_processing_same_sqs_message_is_reported_for_retry_not_deleted(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-processing",
            sqs_message_id="sqs-processing",
        )
        self.assertTrue(claimed)

        claimed_again, duplicate_log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-processing",
            sqs_message_id="sqs-processing",
        )

        self.assertFalse(claimed_again)
        self.assertEqual(duplicate_log_id, log_id)

    def test_stale_processing_same_sqs_message_is_reclaimed(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-stale-processing",
            sqs_message_id="sqs-stale-processing",
        )
        self.assertTrue(claimed)
        NotificationLog.objects.filter(id=log_id).update(
            claimed_at=timezone.now() - timedelta(minutes=10)
        )

        reclaimed, reclaimed_log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-stale-processing",
            sqs_message_id="sqs-stale-processing",
        )

        self.assertTrue(reclaimed)
        self.assertEqual(reclaimed_log_id, log_id)

    def test_failed_business_slot_from_different_sqs_message_remains_duplicate(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-other-message",
            sqs_message_id="sqs-original",
        )
        self.assertTrue(claimed)
        finalize_notification(log_id, success=False, failure_reason="permanent_failure")

        claimed_again, duplicate_log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-other-message",
            sqs_message_id="sqs-new",
        )

        self.assertFalse(claimed_again)
        self.assertIsNone(duplicate_log_id)
