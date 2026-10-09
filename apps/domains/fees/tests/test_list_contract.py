from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import TenantMembership
from apps.domains.fees.models import FeePayment, FeeTemplate
from apps.domains.fees.tests.test_payment_lifecycle import FeesTestMixin


class FeesListContractTests(FeesTestMixin, APITestCase):
    def setUp(self):
        self.tenant = self.make_tenant("fee_list_contract")
        self.other = self.make_tenant("fee_list_foreign")
        owner = get_user_model().objects.create_user(username="fee_list_owner", tenant=self.tenant, is_staff=True)
        TenantMembership.objects.create(tenant=self.tenant, user=owner, role="owner", is_active=True)
        self.client.force_authenticate(owner)
        self.headers = {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant.code}
        self.student = self.make_student(self.tenant)
        self.invoice = self.make_invoice(self.tenant, self.student)

    def test_equal_timestamp_templates_and_payments_page_newest_first_without_foreign_rows(self):
        when = timezone.now()
        template_ids = [FeeTemplate.objects.create(
            tenant=self.tenant, name=f"검증 비목 {index}", fee_type="TUITION", amount=100,
        ).id for index in range(5)]
        FeeTemplate.objects.filter(id__in=template_ids).update(created_at=when)
        self.make_fee_template(self.other)
        payment_ids = [FeePayment.objects.create(
            tenant=self.tenant, student=self.student, invoice=self.invoice,
            amount=100, payment_method="CASH", paid_at=when,
        ).id for _ in range(5)]
        other_student = self.make_student(self.other)
        FeePayment.objects.create(tenant=self.other, student=other_student,
                                  invoice=self.make_invoice(self.other, other_student),
                                  amount=100, payment_method="CASH", paid_at=when)
        for endpoint, ids in [("templates", template_ids), ("payments", payment_ids)]:
            with self.subTest(endpoint=endpoint):
                for _ in range(2):
                    rows = []
                    for page in range(1, 4):
                        response = self.client.get(f"/api/v1/fees/{endpoint}/", {"page": page, "page_size": 2}, **self.headers)
                        self.assertEqual(response.status_code, 200, response.data)
                        self.assertEqual(response.data["count"], 5)
                        rows.extend(item["id"] for item in response.data["results"])
                    self.assertEqual(rows, list(reversed(ids)))

    def test_unpaid_filter_contains_pending_partial_overdue_only(self):
        self.invoice.delete()
        expected = []
        for month, status in enumerate(["PENDING", "PARTIAL", "OVERDUE", "PAID", "CANCELLED"], 1):
            invoice = self.make_invoice(self.tenant, self.student, month=month)
            invoice.status = status
            invoice.save(update_fields=["status"])
            if status in {"PENDING", "PARTIAL", "OVERDUE"}:
                expected.append(invoice.id)
        other_student = self.make_student(self.other)
        self.make_invoice(self.other, other_student)
        response = self.client.get("/api/v1/fees/invoices/", {"status": "UNPAID", "ordering": "unpaid_first"}, **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["id"] for row in response.data["results"]], [expected[2], expected[0], expected[1]])
