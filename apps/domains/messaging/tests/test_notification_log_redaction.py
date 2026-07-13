from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from academy.adapters.db.django.repositories_messaging import (
    claim_notification_slot,
    create_notification_log,
    finalize_notification,
    mark_notification_sending,
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

    def test_target_sanitizer_handles_legacy_formatted_phone(self):
        create_notification_log(
            tenant_id=self.tenant.id,
            success=True,
            amount_deducted=Decimal("0"),
            recipient_summary="010****5678",
            target_id="parent:17:+82-10-1234-5678",
        )

        self.assertEqual(NotificationLog.objects.get().target_id, "parent:17")

    def test_finalize_redacts_sensitive_body_on_claimed_log(self):
        claimed, log_id = claim_notification_slot(
            tenant_id=self.tenant.id,
            message_mode="alimtalk",
            business_idempotency_key="redact-finalize",
            sqs_message_id="sqs-redact",
            target_id="parent:7:01012345678",
        )
        self.assertTrue(claimed)
        self.assertTrue(mark_notification_sending(log_id))

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
        self.assertEqual(log.target_id, "parent:7")

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

    def test_log_api_includes_owner_proxy_logs_for_source_tenant(self):
        owner = Tenant.objects.create(name="Owner Sender", code="owner-sender", is_active=True)
        create_notification_log(
            tenant_id=owner.id,
            source_tenant_id=self.tenant.id,
            success=True,
            amount_deducted=Decimal("1"),
            recipient_summary="010****7466",
            template_summary="가입 안내(학부모)",
            message_body="학부모 가입 안내",
            message_mode="alimtalk",
            provider_message_id="group-source-tenant-proof",
            notification_type="registration_approved_parent",
            target_type="account",
            target_id="parent:123:01031217466",
            target_name="E2E 학생",
        )
        log = NotificationLog.objects.get(provider_message_id="group-source-tenant-proof")
        self.assertEqual(log.target_id, "parent:123")
        self.assertNotIn("01031217466", log.target_id)
        NotificationLog.objects.filter(pk=log.pk).update(
            target_id="parent:123:01031217466"
        )

        list_request = self.factory.get("/api/v1/messaging/log/")
        force_authenticate(list_request, user=self.admin)
        list_request.user = self.admin
        list_request.tenant = self.tenant
        list_response = NotificationLogListView.as_view()(list_request)

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["results"][0]["id"], log.id)
        self.assertEqual(list_response.data["results"][0]["notification_type"], "registration_approved_parent")
        self.assertEqual(list_response.data["results"][0]["source_tenant_id"], self.tenant.id)
        self.assertEqual(list_response.data["results"][0]["target_id"], "parent:123")
        self.assertNotIn("01031217466", str(list_response.data["results"][0]))

        detail_request = self.factory.get(f"/api/v1/messaging/log/{log.id}/")
        force_authenticate(detail_request, user=self.admin)
        detail_request.user = self.admin
        detail_request.tenant = self.tenant
        detail_response = NotificationLogDetailView.as_view()(detail_request, pk=log.id)

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["notification_type"], "registration_approved_parent")
        self.assertEqual(detail_response.data["provider_message_id"], "group-source-tenant-proof")
        self.assertEqual(detail_response.data["target_id"], "parent:123")
        self.assertNotIn("01031217466", str(detail_response.data))

    def test_log_api_scopes_owner_proxy_logs_to_business_tenant(self):
        provider_owner = Tenant.objects.create(name="Provider Owner", code="provider-owner", is_active=True)
        other_customer = Tenant.objects.create(name="Other Customer", code="other-customer", is_active=True)
        owner_staff = User.objects.create_user(
            username="provider-owner-staff",
            password="test1234",
            tenant=provider_owner,
            is_staff=True,
        )
        other_staff = User.objects.create_user(
            username="other-customer-staff",
            password="test1234",
            tenant=other_customer,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=provider_owner, user=owner_staff, role="staff")
        TenantMembership.ensure_active(tenant=other_customer, user=other_staff, role="staff")

        create_notification_log(
            tenant_id=provider_owner.id,
            success=True,
            amount_deducted=Decimal("1"),
            recipient_summary="owner native",
            provider_message_id="owner-native",
        )
        create_notification_log(
            tenant_id=self.tenant.id,
            success=True,
            amount_deducted=Decimal("1"),
            recipient_summary="customer native",
            provider_message_id="customer-native",
        )
        create_notification_log(
            tenant_id=provider_owner.id,
            source_tenant_id=self.tenant.id,
            success=True,
            amount_deducted=Decimal("1"),
            recipient_summary="customer proxied",
            provider_message_id="customer-proxied",
        )
        create_notification_log(
            tenant_id=provider_owner.id,
            source_tenant_id=other_customer.id,
            success=True,
            amount_deducted=Decimal("1"),
            recipient_summary="other proxied",
            provider_message_id="other-proxied",
        )
        customer_proxy_log = NotificationLog.objects.get(provider_message_id="customer-proxied")

        def list_for(user, tenant):
            request = self.factory.get("/api/v1/messaging/log/")
            force_authenticate(request, user=user)
            request.user = user
            request.tenant = tenant
            return NotificationLogListView.as_view()(request)

        owner_response = list_for(owner_staff, provider_owner)
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(
            {item["provider_message_id"] for item in owner_response.data["results"]},
            {"owner-native"},
        )

        customer_response = list_for(self.admin, self.tenant)
        self.assertEqual(customer_response.status_code, 200)
        self.assertEqual(
            {item["provider_message_id"] for item in customer_response.data["results"]},
            {"customer-native", "customer-proxied"},
        )

        owner_detail_request = self.factory.get(f"/api/v1/messaging/log/{customer_proxy_log.id}/")
        force_authenticate(owner_detail_request, user=owner_staff)
        owner_detail_request.user = owner_staff
        owner_detail_request.tenant = provider_owner
        owner_detail = NotificationLogDetailView.as_view()(owner_detail_request, pk=customer_proxy_log.id)
        self.assertEqual(owner_detail.status_code, 404)

        other_detail_request = self.factory.get(f"/api/v1/messaging/log/{customer_proxy_log.id}/")
        force_authenticate(other_detail_request, user=other_staff)
        other_detail_request.user = other_staff
        other_detail_request.tenant = other_customer
        other_detail = NotificationLogDetailView.as_view()(other_detail_request, pk=customer_proxy_log.id)
        self.assertEqual(other_detail.status_code, 404)

        customer_detail_request = self.factory.get(f"/api/v1/messaging/log/{customer_proxy_log.id}/")
        force_authenticate(customer_detail_request, user=self.admin)
        customer_detail_request.user = self.admin
        customer_detail_request.tenant = self.tenant
        customer_detail = NotificationLogDetailView.as_view()(
            customer_detail_request,
            pk=customer_proxy_log.id,
        )
        self.assertEqual(customer_detail.status_code, 200)
