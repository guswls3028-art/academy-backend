from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import TenantMembership
from apps.domains.fees.models import InvoiceItem, StudentFee, StudentInvoice
from apps.domains.fees.services import generate_monthly_invoices
from apps.domains.fees.tests.test_payment_lifecycle import FeesTestMixin
from apps.domains.fees.views import StudentFeeViewSet


class AssignmentUpdateIntegrityTests(FeesTestMixin, TestCase):
    def setUp(self):
        self.tenant = self.make_tenant(code="fee-update-integrity")
        self.student = self.make_student(self.tenant)
        self.template = self.make_fee_template(self.tenant)
        self.owner = get_user_model().objects.create_user(username="fee-update-owner")
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")
        self.fee = StudentFee.objects.create(
            tenant=self.tenant, student=self.student, fee_template=self.template,
            billing_start_month="2026-09", billing_end_month="2026-12",
        )

    def update(self, data):
        request = APIRequestFactory().patch(f"/fees/{self.fee.pk}/", data, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)
        return StudentFeeViewSet.as_view({"patch": "partial_update"})(request, pk=self.fee.pk)

    def test_stale_partial_edit_preserves_new_discount_in_generated_invoice(self):
        stale = StudentFee.objects.get(pk=self.fee.pk)
        response = self.update({"discount_amount": 30000})
        self.assertEqual(response.status_code, 200, response.data)
        with patch.object(StudentFeeViewSet, "get_object", return_value=stale):
            response = self.update({"billing_end_month": "2026-11"})
        self.assertEqual(response.status_code, 200, response.data)
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.discount_amount, 30000)
        self.assertEqual(self.fee.billing_end_month, "2026-11")
        result = generate_monthly_invoices(self.tenant, 2026, 10, date(2026, 10, 31))
        self.assertEqual(result["created"], 1, result)
        invoice = StudentInvoice.objects.get(tenant=self.tenant, student=self.student)
        self.assertEqual(invoice.total_amount, 70000)
        self.assertEqual(InvoiceItem.objects.get(invoice=invoice).amount, 70000)

    def test_stale_period_edit_revalidates_latest_end_and_allows_corrected_retry(self):
        stale = StudentFee.objects.get(pk=self.fee.pk)
        response = self.update({"billing_end_month": "2026-10"})
        self.assertEqual(response.status_code, 200, response.data)
        with patch.object(StudentFeeViewSet, "get_object", return_value=stale):
            response = self.update({"billing_start_month": "2026-11"})
        self.assertEqual(response.status_code, 400, response.data)
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.billing_start_month, "2026-09")
        self.assertEqual(self.fee.billing_end_month, "2026-10")
        response = self.update({"billing_start_month": "2026-10"})
        self.assertEqual(response.status_code, 200, response.data)
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.billing_start_month, "2026-10")
