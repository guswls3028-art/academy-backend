"""계좌이체 입금 신고와 세금계산서 대기열 API 통합 테스트."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.billing.models import (
    BankTransferNotice,
    BusinessProfile,
    Invoice,
    PaymentTransaction,
    TaxInvoiceIssue,
)
from apps.core.models import Tenant, TenantMembership
from apps.core.models.program import Program

User = get_user_model()


@override_settings(
    BILLING_BANK_TRANSFER_ENABLED=True,
    BILLING_BANK_NAME="테스트은행",
    BILLING_BANK_ACCOUNT_NUMBER="123-456-7890",
    BILLING_BANK_ACCOUNT_HOLDER="학원플러스",
    BILLING_EXEMPT_TENANT_IDS=set(),
)
class BankTransferApiTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Bank Academy",
            code="bank_academy",
            is_active=True,
        )
        self.owner_tenant_settings = override_settings(
            OWNER_TENANT_ID=self.tenant.id
        )
        self.owner_tenant_settings.enable()
        self.addCleanup(self.owner_tenant_settings.disable)
        self.other_tenant = Tenant.objects.create(
            name="Other Academy",
            code="other_bank_academy",
            is_active=True,
        )
        self.program = Program.objects.get(tenant=self.tenant)
        self.program.subscription_status = "active"
        self.program.subscription_started_at = date(2026, 7, 1)
        self.program.subscription_expires_at = date(2026, 8, 12)
        self.program.billing_mode = "AUTO_CARD"
        self.program.save()

        self.owner = User.objects.create_user(
            username="bank-owner",
            password="test1234!",
            tenant=self.tenant,
            is_staff=True,
            name="Bank Owner",
        )
        TenantMembership.objects.create(
            user=self.owner,
            tenant=self.tenant,
            role="owner",
            is_active=True,
        )
        self.staff = User.objects.create_user(
            username="bank-staff",
            password="test1234!",
            tenant=self.tenant,
            is_staff=True,
            name="Bank Staff",
        )
        TenantMembership.objects.create(
            user=self.staff,
            tenant=self.tenant,
            role="staff",
            is_active=True,
        )
        self.superuser = User.objects.create_superuser(
            username="bank-super",
            password="test1234!",
            tenant=self.tenant,
            name="Bank Super",
        )
        TenantMembership.objects.create(
            user=self.superuser,
            tenant=self.tenant,
            role="owner",
            is_active=True,
        )
        self.headers = {
            "HTTP_HOST": "localhost",
            "HTTP_X_TENANT_CODE": self.tenant.code,
        }
        self.invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-BANK-001",
            plan="all",
            billing_mode="AUTO_CARD",
            supply_amount=180_000,
            tax_amount=18_000,
            total_amount=198_000,
            period_start=date(2026, 8, 13),
            period_end=date(2026, 9, 12),
            due_date=date(2026, 8, 13),
            status="SCHEDULED",
        )

    def _save_business_profile(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            "/api/v1/billing/business-profile/",
            {
                "business_name": "테스트학원",
                "representative_name": "홍길동",
                "business_registration_number": "608-35-75724",
                "address": "서울시 송파구",
                "business_type": "교육서비스업",
                "business_item": "학원",
                "tax_invoice_email": "tax@example.com",
                "manager_name": "홍길동",
                "manager_phone": "01012345678",
                "manager_email": "owner@example.com",
            },
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def _activate(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/billing/bank-transfer/activate/",
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def _submit_notice(self, *, tax_invoice_requested=True):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/billing/bank-transfer/notices/",
            {
                "invoice_id": self.invoice.pk,
                "depositor_name": "홍길동",
                "deposited_at": timezone.now().isoformat(),
                "tax_invoice_requested": tax_invoice_requested,
            },
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def test_owner_activates_bank_transfer_and_sees_account(self):
        response = self._activate()

        self.program.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.program.billing_mode, "INVOICE_REQUEST")
        self.assertEqual(self.invoice.billing_mode, "INVOICE_REQUEST")
        self.assertEqual(self.invoice.status, "PENDING")
        self.assertEqual(
            self.invoice.due_date,
            self.invoice.period_start + timedelta(days=15),
        )
        self.assertEqual(
            response.data["bank_account"]["account_number"],
            "123-456-7890",
        )
        self.assertEqual(
            response.data["invoices"][0]["invoice_number"],
            "INV-BANK-001",
        )
        from apps.billing.services.payment_service import _claim_payment_attempt

        claim = _claim_payment_attempt(self.invoice.pk)
        self.assertFalse(claim["claimed"])
        self.assertEqual(claim["reason"], "billing_mode_changed")

    def test_business_profile_is_validated_and_normalized(self):
        response = self._save_business_profile()

        self.assertEqual(
            response.data["business_registration_number"],
            "6083575724",
        )
        profile = BusinessProfile.objects.get(tenant=self.tenant)
        self.assertEqual(profile.manager_email, "owner@example.com")
        self.assertEqual(
            profile.to_snapshot()["manager_email"],
            "owner@example.com",
        )

    def test_staff_cannot_change_business_profile_or_submit_notice(self):
        self.client.force_authenticate(self.staff)

        profile_response = self.client.patch(
            "/api/v1/billing/business-profile/",
            {"business_name": "권한없음"},
            format="json",
            **self.headers,
        )
        notice_response = self.client.post(
            "/api/v1/billing/bank-transfer/notices/",
            {
                "invoice_id": self.invoice.pk,
                "depositor_name": "권한없음",
                "deposited_at": timezone.now().isoformat(),
                "tax_invoice_requested": False,
            },
            format="json",
            **self.headers,
        )

        self.assertEqual(profile_response.status_code, 403)
        self.assertEqual(notice_response.status_code, 403)

    def test_tax_invoice_request_requires_business_profile(self):
        self._activate()
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            "/api/v1/billing/bank-transfer/notices/",
            {
                "invoice_id": self.invoice.pk,
                "depositor_name": "홍길동",
                "deposited_at": timezone.now().isoformat(),
                "tax_invoice_requested": True,
            },
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(
            BankTransferNotice.objects.filter(invoice=self.invoice).exists()
        )

    def test_confirm_is_atomic_idempotent_and_queues_tax_invoice(self):
        self._activate()
        self._save_business_profile()
        notice_response = self._submit_notice()
        notice_id = notice_response.data["id"]

        self.client.force_authenticate(self.superuser)
        response = self.client.post(
            f"/api/v1/billing/admin/bank-transfer/notices/{notice_id}/confirm/",
            format="json",
            **self.headers,
        )
        retry_response = self.client.post(
            f"/api/v1/billing/admin/bank-transfer/notices/{notice_id}/confirm/",
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(retry_response.status_code, 200, retry_response.data)
        self.invoice.refresh_from_db()
        notice = BankTransferNotice.objects.get(pk=notice_id)
        issue = TaxInvoiceIssue.objects.get(invoice=self.invoice)
        self.assertEqual(self.invoice.status, "PAID")
        self.assertEqual(notice.status, "CONFIRMED")
        self.assertEqual(issue.status, "READY")
        self.assertEqual(
            PaymentTransaction.objects.filter(invoice=self.invoice).count(),
            1,
        )

    def test_generic_mark_paid_cannot_bypass_submitted_notice(self):
        self._activate()
        notice_id = self._submit_notice(
            tax_invoice_requested=False
        ).data["id"]
        self.client.force_authenticate(self.superuser)

        response = self.client.post(
            f"/api/v1/billing/admin/invoices/{self.invoice.pk}/mark-paid/",
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 409)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PENDING")
        self.assertEqual(
            BankTransferNotice.objects.get(pk=notice_id).status,
            "SUBMITTED",
        )
        self.assertFalse(
            PaymentTransaction.objects.filter(invoice=self.invoice).exists()
        )

    def test_confirm_rejects_invoice_amount_drift(self):
        self._activate()
        notice_id = self._submit_notice(
            tax_invoice_requested=False
        ).data["id"]
        Invoice.objects.filter(pk=self.invoice.pk).update(total_amount=199_000)
        self.client.force_authenticate(self.superuser)

        response = self.client.post(
            f"/api/v1/billing/admin/bank-transfer/notices/{notice_id}/confirm/",
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 409)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "PENDING")
        self.assertFalse(
            PaymentTransaction.objects.filter(invoice=self.invoice).exists()
        )

    def test_future_invoice_cannot_skip_earlier_unpaid_period(self):
        future_invoice = Invoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-BANK-002",
            plan="all",
            billing_mode="AUTO_CARD",
            supply_amount=180_000,
            tax_amount=18_000,
            total_amount=198_000,
            period_start=date(2026, 9, 13),
            period_end=date(2026, 10, 12),
            due_date=date(2026, 9, 13),
            status="SCHEDULED",
        )
        activate_response = self._activate()
        self.assertEqual(
            activate_response.data["invoices"][0]["id"],
            self.invoice.pk,
        )
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            "/api/v1/billing/bank-transfer/notices/",
            {
                "invoice_id": future_invoice.pk,
                "depositor_name": "홍길동",
                "deposited_at": timezone.now().isoformat(),
                "tax_invoice_requested": False,
            },
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(
            BankTransferNotice.objects.filter(invoice=future_invoice).exists()
        )

    def test_operator_can_record_hometax_issue_number(self):
        self._activate()
        self._save_business_profile()
        notice_id = self._submit_notice().data["id"]
        self.client.force_authenticate(self.superuser)
        self.client.post(
            f"/api/v1/billing/admin/bank-transfer/notices/{notice_id}/confirm/",
            format="json",
            **self.headers,
        )
        issue = TaxInvoiceIssue.objects.get(invoice=self.invoice)

        response = self.client.post(
            f"/api/v1/billing/admin/tax-invoices/{issue.pk}/mark-issued/",
            {"issue_number": "202607271234567890123456"},
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 200, response.data)
        issue.refresh_from_db()
        self.assertEqual(issue.status, "ISSUED")
        self.assertEqual(issue.issue_number, "202607271234567890123456")

    def test_notice_cannot_cross_tenant_boundary(self):
        self._activate()
        other_invoice = Invoice.objects.create(
            tenant=self.other_tenant,
            invoice_number="INV-BANK-OTHER",
            plan="all",
            billing_mode="INVOICE_REQUEST",
            supply_amount=10_000,
            tax_amount=1_000,
            total_amount=11_000,
            period_start=date(2026, 8, 13),
            period_end=date(2026, 9, 12),
            due_date=date(2026, 8, 28),
            status="PENDING",
        )
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            "/api/v1/billing/bank-transfer/notices/",
            {
                "invoice_id": other_invoice.pk,
                "depositor_name": "홍길동",
                "deposited_at": timezone.now().isoformat(),
                "tax_invoice_requested": False,
            },
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_platform_queue_requires_owner_tenant_host(self):
        self.client.force_authenticate(self.superuser)
        response = self.client.get(
            "/api/v1/billing/admin/bank-transfer/notices/",
            HTTP_HOST="localhost",
            HTTP_X_TENANT_CODE=self.other_tenant.code,
        )

        self.assertEqual(response.status_code, 403)
