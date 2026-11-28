"""In-process HTTP checks for the disposable billing image probe. No TCP listener."""

import os

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.qa_settlement import SETTLEMENT_SETTINGS, SettlementFixtureMixin
from apps.core.models import Tenant


@override_settings(**SETTLEMENT_SETTINGS)
class ImageSettlementProbe(SettlementFixtureMixin, TransactionTestCase):
    def login(self, actor):
        client = APIClient(HTTP_HOST="127.0.0.1", HTTP_X_TENANT_CODE=actor["code"])
        login = client.post("/api/v1/token/", {
            "tenant_code": actor["code"], "username": actor["username"], "password": actor["password"],
        }, format="json")
        self.assertEqual(login.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + login.json()["access"])
        me = client.get("/api/v1/core/me/").json()
        self.assertEqual((me["tenantRole"], me["is_superuser"]), ("owner", False))
        program = client.get("/api/v1/core/program/").json()
        self.assertEqual((program["tenantCode"], program["isPlatformAdmin"]), (actor["code"], actor["code"] == "9999"))
        return client

    def test_settlement(self):
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA database_list")
            self.assertTrue(all(name in {"main", "temp"} and not filename
                                for _, name, filename in cursor.fetchall()))
        self.assertFalse(Tenant.objects.exists() or get_user_model().objects.exists())
        self.exercise_settlement(self.submit_and_review)

    def submit_and_review(self, platform, schools):
        reviewer = self.login(platform)
        # The supervisor can distinguish a lost worker from a completed readback.
        print(f"BILLING_PROBE_FIXTURE_READY {os.getpid()}", flush=True)
        transfer = "/api/v1/billing/bank-transfer/"
        queue = "/api/v1/billing/admin/bank-transfer/notices/"
        for school in schools:
            owner = self.login(school)
            if school["manual"]:
                profile = owner.patch("/api/v1/billing/business-profile/", {
                    "business_name": "QA Academy", "representative_name": "QA Owner",
                    "business_registration_number": "0000000107", "tax_invoice_email": "qa@example.invalid",
                }, format="json")
                self.assertEqual(profile.status_code, 200)
            activated = owner.post(transfer + "activate/", {}, format="json")
            self.assertEqual(activated.status_code, 200)
            [invoice] = activated.json()["invoices"]
            response = owner.post(transfer + "notices/", {
                "invoice_id": invoice["id"], "depositor_name": "QA only",
                "deposited_at": timezone.now().isoformat(), "tax_invoice_requested": school["manual"],
            }, format="json")
            self.assertEqual(response.status_code, 201)
            notice = response.json()
            self.assertEqual((notice["status"], notice["automatic_receipt"]), ("SUBMITTED", False))
            confirm = f"{queue}{notice['id']}/confirm/"
            self.assertEqual(owner.post(confirm).status_code, 403)
            for _ in range(2):
                approved = reviewer.post(confirm)
                self.assertEqual(approved.status_code, 200)
                self.assertEqual(approved.json()["status"], "CONFIRMED")
            [paid] = owner.get(transfer).json()["invoices"]
            self.assertEqual((paid["id"], paid["status"]), (invoice["id"], "PAID"))
            self.assertEqual(paid["bank_transfer_notice"]["tax_invoice_status"],
                             "READY" if school["manual"] else "NOT_REQUESTED")
            pending = reviewer.get(queue, {"actionable": "true"}).json()["results"]
            self.assertEqual(any(item["id"] == notice["id"] for item in pending), school["manual"])
