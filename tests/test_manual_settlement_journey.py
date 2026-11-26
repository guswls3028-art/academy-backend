"""Exercise manual settlement over loopback with two independent tenant-owner logins."""

import json
import os
import secrets
from io import StringIO
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from apps.billing.models import BankTransferNotice, BusinessProfile, Invoice, PaymentTransaction, TaxInvoiceIssue
from apps.core.models import Tenant, TenantMembership


@override_settings(
    BILLING_BANK_TRANSFER_ENABLED=True,
    BILLING_BANK_NAME="QA bank",
    BILLING_BANK_ACCOUNT_NUMBER="000-000-000",
    BILLING_BANK_ACCOUNT_HOLDER="QA only",
    BILLING_EXEMPT_TENANT_IDS=set(),
    BILLING_POPBILL_MODE="off",
)
class ManualSettlementJourneyTests(LiveServerTestCase):
    host = "127.0.0.1"

    def scenario(self, code, *, destroy=False):
        output = StringIO()
        call_command(
            "setup_ymath_realuse_scenario", tenant_code=code, student_count=1,
            session_count=1, destroy=destroy, stdout=output,
        )
        return json.loads(output.getvalue())

    def login(self, code, password):
        client = requests.Session()
        self.addCleanup(client.close)
        client.trust_env = False
        client.headers["X-Tenant-Code"] = code
        tokens = self.api(client, "POST", "/token/", {
            "tenant_code": code, "username": "ymath-qa-organization-owner", "password": password,
        })
        client.headers["Authorization"] = f"Bearer {tokens['access']}"
        return client

    def api(self, client, method, path, data=None, *, status=200):
        response = client.request(
            method, f"{self.live_server_url}/api/v1{path}", json=data, timeout=10, allow_redirects=False,
        )
        self.assertEqual(response.status_code, status, f"{method} {path}")
        return response.json()

    def test_owner_submits_operator_confirms_and_owner_reloads(self):
        school_code = "qa-ymath-realuse-bank-school"
        platform_code = "qa-ymath-realuse-bank-ops"
        owner_password = secrets.token_urlsafe(24)
        operator_password = secrets.token_urlsafe(24)
        with patch.dict(os.environ, {"YMATH_REALUSE_SCENARIO_PASSWORD": owner_password}):
            self.scenario(school_code)
        with patch.dict(os.environ, {"YMATH_REALUSE_SCENARIO_PASSWORD": operator_password}):
            self.scenario(platform_code)
        school = Tenant.objects.get(code=school_code)
        platform = Tenant.objects.get(code=platform_code)
        operator = TenantMembership.objects.get(tenant=platform, role="owner", is_active=True).user
        self.assertFalse(operator.is_superuser)
        school_users = list(get_user_model().objects.filter(tenant=school).values_list("pk", flat=True))

        self.assertFalse(Invoice.objects.filter(tenant=school).exists())
        transfer = "/billing/bank-transfer/"
        queue = "/billing/admin/bank-transfer/notices/"
        with self.settings(OWNER_TENANT_ID=platform.pk):
            owner = self.login(school_code, owner_password)
            reviewer = self.login(platform_code, operator_password)
            self.api(owner, "GET", queue, status=403)
            self.api(owner, "PATCH", "/billing/business-profile/", {
                "business_name": "QA Academy", "representative_name": "QA Owner",
                "business_registration_number": "0000000107", "tax_invoice_email": "qa@example.invalid",
            })
            activated = self.api(owner, "POST", transfer + "activate/")
            [invoice] = activated["invoices"]
            self.assertEqual(invoice["status"], "PENDING")
            submitted = self.api(owner, "POST", transfer + "notices/", {
                "invoice_id": invoice["id"], "depositor_name": "QA depositor",
                "deposited_at": timezone.now().isoformat(), "tax_invoice_requested": True,
            }, status=201)
            self.assertEqual(submitted["status"], "SUBMITTED")
            [pending] = self.api(owner, "GET", transfer)["invoices"]
            self.assertEqual(pending["bank_transfer_notice"]["id"], submitted["id"])
            self.assertEqual(pending["bank_transfer_notice"]["status"], "SUBMITTED")
            items = self.api(reviewer, "GET", queue + "?status=SUBMITTED")["results"]
            self.assertIn((submitted["id"], school_code), [(item["id"], item["tenant_code"]) for item in items])
            confirm = f"{queue}{submitted['id']}/confirm/"
            self.api(owner, "POST", confirm, status=403)
            # The candidate launches its provider SDK via a subprocess. Block that boundary
            # while exercising both the original approval and the replay.
            with patch("subprocess.run", side_effect=AssertionError("Unexpected provider process")) as external:
                for _ in range(2):
                    confirmed = self.api(reviewer, "POST", confirm)
                    self.assertEqual(confirmed["status"], "CONFIRMED")
                external.assert_not_called()
            [reloaded] = self.api(owner, "GET", transfer)["invoices"]
            self.assertEqual((reloaded["id"], reloaded["status"]), (invoice["id"], "PAID"))
            notice = reloaded["bank_transfer_notice"]
            self.assertEqual((notice["id"], notice["status"], notice["tax_invoice_status"]),
                             (submitted["id"], "CONFIRMED", "READY"))
            self.assertEqual(BankTransferNotice.objects.get(pk=notice["id"]).reviewed_by_id, operator.pk)
            [payment_id] = PaymentTransaction.objects.filter(invoice_id=invoice["id"]).values_list("pk", flat=True)
            issue = TaxInvoiceIssue.objects.get(invoice_id=invoice["id"])
            self.assertEqual(issue.status, "READY")

        credentials = list(OutstandingToken.objects.filter(user_id__in=school_users).values_list("pk", flat=True))
        self.assertTrue(credentials)
        result = self.scenario(school_code, destroy=True)
        self.assertEqual(result["remaining"], {"tenants": 0, "users": 0})
        self.assertFalse(any(result["residue"].values()))
        self.assertFalse(get_user_model().objects.filter(pk__in=school_users).exists())
        self.assertFalse(OutstandingToken.objects.filter(pk__in=credentials).exists())
        self.assertFalse(Invoice.objects.filter(pk=invoice["id"]).exists())
        self.assertFalse(BankTransferNotice.objects.filter(pk=notice["id"]).exists())
        self.assertFalse(PaymentTransaction.objects.filter(pk=payment_id).exists())
        self.assertFalse(TaxInvoiceIssue.objects.filter(pk=issue.pk).exists())
        self.assertFalse(BusinessProfile.objects.filter(tenant_id=school.pk).exists())
        self.assertTrue(TenantMembership.objects.filter(user=operator, tenant=platform, is_active=True).exists())
        self.assertEqual(self.scenario(platform_code, destroy=True)["remaining"], {"tenants": 0, "users": 0})
