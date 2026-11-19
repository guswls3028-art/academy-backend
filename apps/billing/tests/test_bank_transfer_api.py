"""계좌이체 입금 신고와 세금계산서 대기열 API 통합 테스트."""

import os
from datetime import date, timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.billing.models import (
    BankTransferNotice,
    BusinessProfile,
    Invoice,
    PaymentTransaction,
    PopbillReceipt,
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

    def test_oldest_unpaid_stays_visible_beyond_history_limit_and_accepts_notice(self):
        self.program.billing_mode = "INVOICE_REQUEST"
        self.program.save(update_fields=["billing_mode", "updated_at"])
        self.invoice.billing_mode = "INVOICE_REQUEST"
        self.invoice.status = "OVERDUE"
        self.invoice.period_start = date(2025, 7, 1)
        self.invoice.period_end = date(2025, 7, 31)
        self.invoice.due_date = date(2025, 7, 15)
        self.invoice.save()
        paid_ids = []
        for index in range(13):
            period = date(2025 + (7 + index) // 12, (7 + index) % 12 + 1, 1)
            paid_ids.append(Invoice.objects.create(
                tenant=self.tenant, invoice_number=f"INV-HISTORY-{index}",
                plan="all", billing_mode="INVOICE_REQUEST", status="PAID",
                supply_amount=180_000, tax_amount=18_000, total_amount=198_000,
                period_start=period, period_end=period + timedelta(days=27), due_date=period,
            ).id)
        foreign = Invoice.objects.create(
            tenant=self.other_tenant, invoice_number="INV-FOREIGN-HISTORY",
            plan="all", billing_mode="INVOICE_REQUEST", status="OVERDUE",
            supply_amount=180_000, tax_amount=18_000, total_amount=198_000,
            period_start=date(2025, 6, 1), period_end=date(2025, 6, 30), due_date=date(2025, 6, 15),
        )
        self.client.force_authenticate(self.owner)
        for _ in range(2):
            response = self.client.get("/api/v1/billing/bank-transfer/", **self.headers)
            self.assertEqual(response.status_code, 200, response.data)
            ids = [row["id"] for row in response.data["invoices"]]
            self.assertEqual(ids[0], self.invoice.id)
            self.assertEqual(set(ids), {self.invoice.id, *paid_ids[-12:]})
            self.assertNotIn(foreign.id, ids)
        submitted = self.client.post("/api/v1/billing/bank-transfer/notices/", {
            "invoice_id": self.invoice.id, "depositor_name": "검증 입금자",
            "deposited_at": timezone.now().isoformat(), "tax_invoice_requested": False,
        }, format="json", **self.headers)
        self.assertEqual(submitted.status_code, 201, submitted.data)
        reloaded = self.client.get("/api/v1/billing/bank-transfer/", **self.headers)
        self.assertEqual(reloaded.data["invoices"][0]["bank_transfer_notice"]["status"], "SUBMITTED")

    def _save_business_profile(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            "/api/v1/billing/business-profile/",
            {
                "business_name": "테스트학원",
                "representative_name": "홍길동",
                "business_registration_number": "000-00-00107",
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
            "0000000107",
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

    def _enable_popbill(self):
        config = override_settings(
            BILLING_POPBILL_MODE="test", BILLING_POPBILL_CORP_NUM="1234567890",
            BILLING_POPBILL_LINK_ID="fixture", BILLING_POPBILL_SECRET_KEY="fixture",
            BILLING_POPBILL_CORP_NAME="QA", BILLING_POPBILL_CEO_NAME="QA",
            BILLING_POPBILL_RECIPIENT_KEYS=Fernet.generate_key().decode(),
        )
        config.enable()
        self.addCleanup(config.disable)

    def test_preprod_never_issues_with_the_production_settings_module(self):
        from apps.billing.services.popbill_receipts import enabled

        self._enable_popbill()
        with (
            override_settings(SETTINGS_MODULE="apps.api.config.settings.prod", BILLING_POPBILL_MODE="live"),
            patch.dict(os.environ, {"ACADEMY_RUNTIME_ENV": "preprod"}),
        ):
            self.assertFalse(enabled())

    def _popbill_notice(self, kind, **fields):
        self.client.force_authenticate(self.owner)
        return self.client.post("/api/v1/billing/bank-transfer/notices/", {
            "invoice_id": self.invoice.pk, "depositor_name": "QA", "deposited_at": timezone.now().isoformat(),
            "receipt_kind": kind, **fields,
        }, format="json", **self.headers)

    def _confirm_receipt(self, notice_id):
        self.client.force_authenticate(self.superuser)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(f"/api/v1/billing/admin/bank-transfer/notices/{notice_id}/confirm/",
                                    format="json", **self.headers)

    def test_cash_receipt_retry_recovers_without_repeating_payment(self):
        from apps.billing.services.popbill_receipts import _document
        self._enable_popbill()
        self._activate()
        phone = "01000000000"
        notice = self._popbill_notice("CASH", recipient_identifier=phone)
        self.assertEqual(notice.status_code, 201, notice.data)
        record = PopbillReceipt.objects.get()
        self.assertNotIn(phone, record.recipient_ciphertext)
        self.assertNotIn(phone, str(notice.data))
        keys = []

        def gateway(row):
            keys.append(row.document_key)
            self.assertEqual(_document(row)["identityNum"], phone)
            if len(keys) == 1:
                raise TimeoutError
            return {"state": 304, "number": "TB0000001"}

        with patch("apps.billing.services.popbill_receipts._exchange", side_effect=gateway):
            for _ in range(3):
                response = self._confirm_receipt(notice.data["id"])
                self.assertEqual(response.status_code, 200)
        self.assertEqual(keys, [record.document_key, record.document_key])
        self.assertEqual(PaymentTransaction.objects.filter(invoice=self.invoice).count(), 1)
        record.refresh_from_db()
        self.assertEqual(record.cash_approval_number, "TB0000001")
        self.assertEqual(record.recipient_ciphertext, "")
        self.assertIsNone(record.next_attempt_at)
        self.client.force_authenticate(self.owner)
        readback = self.client.get("/api/v1/billing/bank-transfer/", **self.headers)
        self.assertEqual(readback.data["invoices"][0]["bank_transfer_notice"]["receipt_kind"], "CASH")
        self.assertEqual(readback.data["invoices"][0]["bank_transfer_notice"]["tax_invoice_issue_number"], "TB0000001")
        self.assertNotIn(phone, str(readback.data))

    def test_personal_tax_invoice_uses_encrypted_recipient_and_provider_result(self):
        from apps.billing.services.popbill_receipts import _document
        self._enable_popbill()
        self._activate()
        number = "0001013000000"
        notice = self._popbill_notice("PERSONAL_TAX", recipient_identifier=number,
                                      recipient_name="QA", recipient_email="fixture@example.com")
        self.assertEqual(notice.status_code, 201, notice.data)
        self.assertNotIn(number, str(notice.data))
        def gateway(row):
            doc = _document(row)
            self.assertEqual((doc["invoiceeType"], doc["invoiceeCorpNum"]), ("개인", number))
            self.assertEqual((doc["purposeType"], doc["totalAmount"]), ("영수", str(self.invoice.total_amount)))
            return {"state": 300, "number": "2026111910000000ab000001"}

        with patch("apps.billing.services.popbill_receipts._exchange", side_effect=gateway) as service:
            self._confirm_receipt(notice.data["id"])
            self.assertEqual(service.call_count, 1)
        record = PopbillReceipt.objects.get()
        self.assertEqual(record.issue.status, "ISSUED")
        self.assertEqual(record.recipient_ciphertext, "")
        self.assertIsNotNone(record.next_attempt_at)

    def test_first_remote_read_can_be_an_nts_rejection(self):
        self._enable_popbill()
        self._activate()
        notice = self._popbill_notice("CASH", recipient_identifier="01000000000")
        self.assertEqual(notice.status_code, 201)
        with patch("apps.billing.services.popbill_receipts._exchange", return_value={"state": 305}):
            self._confirm_receipt(notice.data["id"])
        record = PopbillReceipt.objects.get()
        self.assertEqual((record.issue.status, record.recipient_ciphertext), ("FAILED", ""))
        self.assertEqual(record.issue.invoice.status, "PAID")

    def test_only_first_confirmation_promotes_an_older_business_request(self):
        from apps.billing.services.popbill_receipts import _document
        self._activate()
        self._save_business_profile()
        notice = self._submit_notice()
        self._enable_popbill()
        def gateway(row):
            document = _document(row)
            self.assertEqual(document["invoiceeCorpNum"], "0000000107")
            self.assertEqual(document["invoicerCorpNum"], "1234567890")
            return {"state": 304, "number": "2026111910000000ab000002"}

        with patch("apps.billing.services.popbill_receipts._exchange", side_effect=gateway):
            self._confirm_receipt(notice.data["id"])
        self.assertEqual(PopbillReceipt.objects.get().issue.status, "ISSUED")

    def test_unconfigured_provider_rejects_personal_data(self):
        self._activate()
        notice = self._popbill_notice("CASH", recipient_identifier="01000000000")
        self.assertEqual(notice.status_code, 503)
        self.assertFalse(BankTransferNotice.objects.exists())

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
