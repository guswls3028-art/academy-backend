"""Disposable billing fixture shared by the opt-in browser and image checks."""

import json
import os
import secrets
from contextlib import ExitStack
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, transaction
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from apps.billing.models import BankTransferNotice, BusinessProfile, Invoice, PaymentTransaction, TaxInvoiceIssue
from apps.core.management.commands.setup_ymath_realuse_scenario import assert_isolated_runtime
from apps.core.models import Program, Tenant, TenantMembership
from apps.core.models.user import user_internal_username


SETTLEMENT_SETTINGS = dict(
    BILLING_BANK_TRANSFER_ENABLED=True,
    BILLING_BANK_NAME="QA bank",
    BILLING_BANK_ACCOUNT_NUMBER="000-000-000",
    BILLING_BANK_ACCOUNT_HOLDER="QA only",
    BILLING_EXEMPT_TENANT_IDS=set(),
    BILLING_POPBILL_MODE="off",
    TOSS_AUTO_BILLING_ENABLED=False,
    CORS_ALLOWED_ORIGIN_REGEXES=[r"^http://127\.0\.0\.1:[0-9]+$"],
)


class SettlementFixtureMixin:
    @classmethod
    def setUpClass(cls):
        assert_isolated_runtime()
        if not (settings.SETTINGS_MODULE == "apps.api.config.settings.test" and
                connection.vendor == "sqlite" and connection.is_in_memory_db()):
            raise RuntimeError("Billing browser QA requires an in-memory Django test database.")
        super().setUpClass()

    def scenario(self, code, *, destroy=False):
        out = StringIO()
        call_command("setup_ymath_realuse_scenario", tenant_code=code, student_count=1,
                     session_count=1, destroy=destroy, stdout=out)
        return json.loads(out.getvalue())

    def provision(self, cleanup, code):
        self.assertFalse(Tenant.objects.filter(code__iexact=code).exists())
        password = secrets.token_urlsafe(24)
        with patch.dict(os.environ, {"YMATH_REALUSE_SCENARIO_PASSWORD": password}):
            info = self.scenario(code)
        tenant_id = info["tenant_id"]
        cleanup.callback(self.destroy_fixture, code, tenant_id)
        member = TenantMembership.objects.get(tenant_id=tenant_id, role="owner", is_active=True)
        self.assertFalse(member.user.is_superuser)
        return {"code": code, "username": "ymath-qa-organization-owner", "password": password}, member

    def provision_local_operator(self, cleanup, nonce):
        # The frontend's local development registry reserves this ID and code for the console.
        self.assertFalse(Tenant.objects.filter(pk=9999).exists())
        self.assertFalse(Tenant.objects.filter(code="9999").exists())
        username = "qa-billing-operator-" + nonce
        password = secrets.token_urlsafe(24)
        with transaction.atomic():
            tenant = Tenant.objects.create(pk=9999, code="9999", name="QA local billing platform")
            Program.objects.get_or_create(tenant=tenant)
            user = get_user_model().objects.create_user(
                tenant=tenant, username=user_internal_username(tenant, username), password=password,
            )
            member = TenantMembership.ensure_active(tenant=tenant, user=user, role="owner")
        cleanup.callback(self.destroy_local_operator, user.pk, user.username)
        self.assertFalse(user.is_superuser)
        return {"code": tenant.code, "username": username, "password": password}, member

    def destroy_local_operator(self, user_id, username):
        tenant = Tenant.objects.get(pk=9999, code="9999", name="QA local billing platform")
        self.assertEqual(list(tenant.users.values_list("pk", "username")), [(user_id, username)])
        tokens = list(OutstandingToken.objects.filter(user_id=user_id).values_list("pk", flat=True))
        OutstandingToken.objects.filter(pk__in=tokens).delete()
        tenant.delete()
        self.assertFalse(Tenant.objects.filter(pk=9999).exists())
        self.assertFalse(get_user_model().objects.filter(pk=user_id).exists())
        self.assertFalse(TenantMembership.objects.filter(user_id=user_id).exists())
        self.assertFalse(OutstandingToken.objects.filter(pk__in=tokens).exists())

    def destroy_fixture(self, code, tenant_id):
        self.assertTrue(Tenant.objects.filter(pk=tenant_id, code=code).exists())
        users = list(get_user_model().objects.filter(tenant_id=tenant_id).values_list("pk", flat=True))
        tokens = list(OutstandingToken.objects.filter(user_id__in=users).values_list("pk", flat=True))
        notices = list(BankTransferNotice.objects.filter(invoice__tenant_id=tenant_id).values_list("pk", flat=True))
        result = self.scenario(code, destroy=True)
        self.assertEqual(result["tenant_id"], tenant_id)
        self.assertEqual(result["remaining"], {"tenants": 0, "users": 0})
        self.assertFalse(any(result["residue"].values()))
        self.assertFalse(get_user_model().objects.filter(pk__in=users).exists())
        self.assertFalse(OutstandingToken.objects.filter(pk__in=tokens).exists())
        self.assertFalse(BankTransferNotice.objects.filter(pk__in=notices).exists())
        for model in (Invoice, PaymentTransaction, TaxInvoiceIssue, BusinessProfile):
            self.assertFalse(model.objects.filter(tenant_id=tenant_id).exists(), model.__name__)

    def exercise_settlement(self, action):
        nonce = secrets.token_hex(5)
        prefix = "qa-ymath-realuse-bank-" + nonce
        receipt = apps.get_model("billing", "PopbillReceipt")
        billing_models = (BankTransferNotice, Invoice, PaymentTransaction, TaxInvoiceIssue, BusinessProfile, receipt)
        self.assertFalse(any(model.objects.exists() for model in billing_models))
        with ExitStack() as cleanup:
            # Registered first, so this independent readback also runs last after an exception.
            cleanup.callback(lambda: self.assertFalse(any(model.objects.exists() for model in billing_models)))
            platform, operator = self.provision_local_operator(cleanup, nonce)
            schools = []
            for suffix, manual in (("-none", False), ("-tax", True)):
                school, owner = self.provision(cleanup, prefix + suffix)
                self.assertFalse(Invoice.objects.filter(tenant_id=owner.tenant_id).exists())
                schools.append(({**school, "manual": manual}, owner.tenant_id))
            # No vendor account, credentials or adapter traffic belongs to either flow.
            with self.settings(OWNER_TENANT_ID=operator.tenant_id), patch(
                "apps.billing.services.popbill_receipts._exchange",
                side_effect=AssertionError("Unexpected provider call"),
            ) as exchange:
                action(platform, [school for school, _ in schools])
                exchange.assert_not_called()
            for school, tenant_id in schools:
                invoice = Invoice.objects.get(tenant_id=tenant_id)
                notice = BankTransferNotice.objects.get(invoice=invoice)
                self.assertEqual((invoice.status, notice.status, notice.reviewed_by_id),
                                 ("PAID", "CONFIRMED", operator.user_id))
                self.assertEqual(notice.tax_invoice_requested, school["manual"])
                payment = PaymentTransaction.objects.get(tenant_id=tenant_id, invoice=invoice)
                self.assertEqual(payment.status, "SUCCESS")
                issues = TaxInvoiceIssue.objects.filter(invoice=invoice)
                self.assertEqual(list(issues.values_list("status", flat=True)), ["READY"] if school["manual"] else [])
            self.assertFalse(receipt.objects.exists())
