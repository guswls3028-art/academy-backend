"""Integration checks for the actual payroll PDF/XLSX artifacts and frozen amounts."""
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.core.models import Tenant, TenantMembership
from apps.domains.staffs.models import PayrollSnapshot, Staff, WorkMonthLock
from apps.domains.staffs.views import PayrollSnapshotViewSet, StaffViewSet


class TestStaffDeductionExports(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="급여 파일 검증", code="payroll-export-test")
        self.owner = get_user_model().objects.create_user(username="export-owner", password="1234")
        TenantMembership.objects.create(tenant=self.tenant, user=self.owner, role="owner", is_active=True)
        self.factory = APIRequestFactory()

    def _staff(self, name):
        return Staff.objects.create(tenant=self.tenant, name=name, phone="")

    def _request(self, method, path, data=None):
        request = getattr(self.factory, method)(path, data or {}, format="json")
        request.tenant = self.tenant
        request.user = self.owner
        return request

    def test_default_deduction_exports_match_frozen_payroll_and_leave_refunds_untaxed(self):
        import fitz
        from openpyxl import load_workbook
        from academy.application.use_cases.ai.pipelines.excel_export_handler import handle_staff_excel_export

        staff = self._staff("=급여 검증")
        snapshot = PayrollSnapshot.objects.create(
            tenant=self.tenant, staff=staff, year=2026, month=8,
            work_hours="25.00", work_amount=308_625,
            approved_expense_amount=2_500, total_amount=311_125,
            generated_by=self.owner,
        )
        expected = {
            "business_income_tax": 9_259, "local_income_tax": 926,
            "deduction_total": 10_185, "net_work_amount": 298_440,
            "transfer_amount": 300_940,
        }
        self.assertEqual(snapshot.default_deduction, expected)
        # Current employment/wage changes must never reprice a closed month's export.
        Staff.objects.filter(pk=staff.pk).update(is_active=False)
        response = PayrollSnapshotViewSet.as_view({"get": "retrieve"})(
            self._request("get", f"/staffs/payroll-snapshots/{snapshot.pk}/"), pk=snapshot.pk,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["default_deduction"], expected)
        pdf = PayrollSnapshotViewSet.as_view({"get": "export_pdf"})(
            self._request("get", f"/staffs/payroll-snapshots/export-pdf/?staff={staff.pk}&year=2026&month=8"),
        )
        self.assertEqual(pdf.status_code, 200)
        with fitz.open(stream=pdf.content, filetype="pdf") as document:
            pdf_text = "".join(page.get_text() for page in document)
        for value in ("기본 공제 3.3%", "10,185", "298,440", "2,500", "300,940", "이체 예정액"):
            self.assertIn(value, pdf_text)

        revision = hashlib.sha256(f"deduction-v2:{snapshot.pk}".encode()).hexdigest()[:16]
        job = SimpleNamespace(id="deduction-export-test", tenant_id=str(self.tenant.pk), payload={
            "year": 2026, "month": 8, "snapshot_ids": [snapshot.pk], "revision": revision, "format_version": 2,
        })
        with patch("academy.application.use_cases.ai.pipelines.excel_export_handler._upload_and_presign") as upload:
            upload.return_value = "https://example.invalid/payroll.xlsx"
            result = handle_staff_excel_export(job)
            self.assertEqual(result.status, "DONE", result.error)
            workbook = load_workbook(BytesIO(upload.call_args.kwargs["fileobj"].getvalue()))
        sheet = workbook.active
        self.assertEqual([cell.value for cell in sheet[1]][4:10], [
            "공제 전 급여", "기본 공제 3.3%", "공제 후 급여", "승인 선결제 환급", "이체 예정액", "정산 합계(공제 전)",
        ])
        self.assertEqual([cell.value for cell in sheet[2]][4:10], [308_625, 10_185, 298_440, 2_500, 300_940, 311_125])
        self.assertEqual(sheet["A2"].value, "=급여 검증")
        self.assertEqual(sheet["A2"].data_type, "s")
        with patch("academy.application.use_cases.ai.pipelines.excel_export_handler._upload_and_presign") as upload:
            job.payload["revision"] = "invalid-revision"
            self.assertEqual(handle_staff_excel_export(job).status, "FAILED")
            upload.assert_not_called()
            # Jobs queued before the rolling update remain consumable.
            del job.payload["format_version"]
            job.payload["revision"] = hashlib.sha256(str(snapshot.pk).encode()).hexdigest()[:16]
            upload.return_value = "https://example.invalid/legacy-queued.xlsx"
            self.assertEqual(handle_staff_excel_export(job).status, "DONE")
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.total_amount, 311_125)

    def test_default_deduction_totals_sum_staff_rounding_and_refund_only_amounts(self):
        for name, work, refund in (("소액 갑", 15, 0), ("소액 을", 15, 0), ("환급만", 0, 10_000)):
            staff = self._staff(name)
            PayrollSnapshot.objects.create(
                tenant=self.tenant, staff=staff, year=2026, month=8,
                work_amount=work, approved_expense_amount=refund, total_amount=work + refund,
            )
            WorkMonthLock.objects.create(tenant=self.tenant, staff=staff, year=2026, month=8)
        response = StaffViewSet.as_view({"get": "payroll_overview"})(
            self._request("get", "/staffs/payroll-overview/?year=2026&month=8"),
        )
        self.assertEqual(response.status_code, 200, response.data)
        # 15 * 3.3% rounds to 0 for each employee; rounding 30 * 3.3% would wrongly deduct 1.
        totals = response.data["totals"]
        self.assertEqual(totals["reference_deduction_total"], 0)
        self.assertEqual(totals["reference_net_work_amount"], 30)
        self.assertEqual(totals["reference_transfer_amount"], 10_030)
