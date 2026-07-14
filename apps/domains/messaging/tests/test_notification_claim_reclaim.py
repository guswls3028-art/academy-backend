from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from academy.adapters.db.django.repositories_messaging import (
    claim_notification_slot,
    finalize_notification,
    mark_notification_sending,
)
from apps.core.models import Tenant
from apps.domains.messaging.credit_services import (
    reserve_notification_credits,
    rollback_notification_credits,
)
from apps.domains.messaging.models import NotificationLog


class NotificationClaimReclaimTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Messaging", code="msgclaim", is_active=True)

    def test_non_duplicate_integrity_error_is_not_swallowed(self):
        with self.assertRaises(IntegrityError):
            claim_notification_slot(
                tenant_id=999_999_999,
                message_mode="alimtalk",
                business_idempotency_key="invalid-tenant-fk",
                sqs_message_id="sqs-invalid-tenant",
            )

    def test_same_sqs_message_can_reclaim_retryable_failed_business_slot(self):
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
            failure_status="retryable_failed",
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

    def test_retryable_failed_slot_can_be_reclaimed_by_duplicate_sqs_delivery(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-retry-new-sqs",
            sqs_message_id="sqs-original",
        )
        self.assertTrue(claimed)
        finalize_notification(
            log_id,
            success=False,
            failure_reason="provider_not_called",
            failure_status="retryable_failed",
        )

        reclaimed, reclaimed_log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-retry-new-sqs",
            sqs_message_id="sqs-duplicate",
        )

        self.assertTrue(reclaimed)
        self.assertEqual(reclaimed_log_id, log_id)
        self.assertEqual(
            NotificationLog.objects.get(id=log_id).sqs_message_id,
            "sqs-duplicate",
        )

    def test_sent_business_slot_remains_duplicate(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-sent",
            sqs_message_id="sqs-2",
        )
        self.assertTrue(claimed)
        self.assertTrue(mark_notification_sending(log_id))
        finalize_notification(log_id, success=True, amount_deducted=Decimal("10"))

        claimed_again, duplicate_log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-sent",
            sqs_message_id="sqs-2",
        )

        self.assertFalse(claimed_again)
        self.assertIsNone(duplicate_log_id)

    def test_success_cannot_skip_provider_sending_boundary(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-invalid-transition",
            sqs_message_id="sqs-invalid-transition",
        )
        self.assertTrue(claimed)

        with self.assertRaises(ValueError):
            finalize_notification(log_id, success=True)

        self.assertEqual(NotificationLog.objects.get(id=log_id).status, "processing")

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

    def test_same_sqs_redelivery_surfaces_sending_slot_as_ambiguous_without_refund(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-provider-boundary",
            sqs_message_id="sqs-provider-boundary",
        )
        self.assertTrue(claimed)
        self.assertTrue(mark_notification_sending(log_id))
        NotificationLog.objects.filter(id=log_id).update(
            claimed_at=timezone.now() - timedelta(days=1),
            amount_deducted=Decimal("10"),
        )

        reclaimed, duplicate_log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-provider-boundary",
            sqs_message_id="sqs-provider-boundary",
            stale_after_seconds=1,
        )

        self.assertFalse(reclaimed)
        self.assertIsNone(duplicate_log_id)
        log = NotificationLog.objects.get(id=log_id)
        self.assertEqual(log.status, "ambiguous")
        self.assertEqual(log.amount_deducted, Decimal("10"))
        self.assertEqual(
            log.failure_reason,
            "provider_result_unresolved_after_sqs_redelivery",
        )

    def test_different_sqs_duplicate_does_not_interrupt_active_sending_slot(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-active-provider-boundary",
            sqs_message_id="sqs-active-original",
        )
        self.assertTrue(claimed)
        self.assertTrue(mark_notification_sending(log_id))

        reclaimed, duplicate_log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-active-provider-boundary",
            sqs_message_id="sqs-active-duplicate",
        )

        self.assertFalse(reclaimed)
        self.assertIsNone(duplicate_log_id)
        self.assertEqual(NotificationLog.objects.get(id=log_id).status, "sending")

    def test_ambiguous_provider_result_is_never_reclaimed(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-ambiguous",
            sqs_message_id="sqs-ambiguous",
        )
        self.assertTrue(claimed)
        self.assertTrue(mark_notification_sending(log_id))
        finalize_notification(
            log_id,
            success=False,
            failure_reason="provider_timeout",
            failure_status="ambiguous",
        )

        reclaimed, duplicate_log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-ambiguous",
            sqs_message_id="sqs-ambiguous",
        )

        self.assertFalse(reclaimed)
        self.assertIsNone(duplicate_log_id)

    def test_notification_credit_reservation_and_rollback_are_idempotent(self):
        self.tenant.credit_balance = Decimal("100")
        self.tenant.save(update_fields=["credit_balance"])
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-credit",
            sqs_message_id="sqs-credit",
        )
        self.assertTrue(claimed)

        reserve_notification_credits(
            notification_log_id=log_id,
            billing_tenant_id=self.tenant.id,
            amount=Decimal("10"),
        )
        NotificationLog.objects.filter(id=log_id).update(
            claimed_at=timezone.now() - timedelta(minutes=10)
        )
        reclaimed, reclaimed_log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="key-credit",
            sqs_message_id="sqs-credit",
        )
        self.assertTrue(reclaimed)
        self.assertEqual(reclaimed_log_id, log_id)
        reserve_notification_credits(
            notification_log_id=log_id,
            billing_tenant_id=self.tenant.id,
            amount=Decimal("10"),
        )
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.credit_balance, Decimal("90"))

        rollback_notification_credits(
            notification_log_id=log_id,
            billing_tenant_id=self.tenant.id,
        )
        rollback_notification_credits(
            notification_log_id=log_id,
            billing_tenant_id=self.tenant.id,
        )
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.credit_balance, Decimal("100"))
        self.assertEqual(
            NotificationLog.objects.get(id=log_id).amount_deducted,
            Decimal("0"),
        )

    def test_common_channel_delivery_bills_and_refunds_source_tenant_only(self):
        owner = self.tenant
        customer = Tenant.objects.create(
            name="Billing Customer",
            code="billing-customer",
            is_active=True,
            credit_balance=Decimal("30"),
        )
        owner.credit_balance = Decimal("100")
        owner.save(update_fields=["credit_balance"])
        claimed, log_id = claim_notification_slot(
            tenant_id=owner.id,
            source_tenant_id=customer.id,
            message_mode="alimtalk",
            business_idempotency_key="key-source-credit",
            sqs_message_id="sqs-source-credit",
        )
        self.assertTrue(claimed)

        reserve_notification_credits(
            notification_log_id=log_id,
            billing_tenant_id=customer.id,
            amount=Decimal("10"),
        )
        owner.refresh_from_db()
        customer.refresh_from_db()
        self.assertEqual(owner.credit_balance, Decimal("100"))
        self.assertEqual(customer.credit_balance, Decimal("20"))

        with self.assertRaisesMessage(ValueError, "notification_billing_tenant_mismatch"):
            rollback_notification_credits(
                notification_log_id=log_id,
                billing_tenant_id=owner.id,
            )

        rollback_notification_credits(
            notification_log_id=log_id,
            billing_tenant_id=customer.id,
        )
        owner.refresh_from_db()
        customer.refresh_from_db()
        self.assertEqual(owner.credit_balance, Decimal("100"))
        self.assertEqual(customer.credit_balance, Decimal("30"))
