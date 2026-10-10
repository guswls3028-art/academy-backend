from datetime import date, time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import OpsAuditLog, Tenant, TenantMembership
from apps.domains.staffs.models import ExpenseRecord, Staff, WorkRecord, WorkType
from apps.domains.staffs.views import ExpenseRecordViewSet, WorkRecordViewSet


class PayrollUpdateIntegrityTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="payroll-integrity", name="QA")
        self.owner = get_user_model().objects.create_user(username="payroll-integrity")
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")
        self.staff = Staff.objects.create(tenant=self.tenant, name="QA staff")
        self.work_type = WorkType.objects.create(
            tenant=self.tenant, name="QA work", base_hourly_wage=15000,
        )
        self.factory = APIRequestFactory()

    def request(self, view, pk, data):
        request = self.factory.patch(f"/records/{pk}/", data, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)
        return view.as_view({"patch": "partial_update"})(request, pk=pk)

    def changed_work_record(self):
        record = WorkRecord.objects.create(
            tenant=self.tenant, staff=self.staff, work_type=self.work_type,
            date=date(2026, 10, 1), end_date=date(2026, 10, 1),
            start_time=time(9), end_time=time(13),
        )
        stale = WorkRecord.objects.get(pk=record.pk)
        response = self.request(WorkRecordViewSet, record.pk, {"end_time": "11:00"})
        self.assertEqual(response.status_code, 200, response.data)
        record.refresh_from_db()
        self.assertEqual(record.amount, 30000)
        return record, stale

    def test_stale_break_edit_cannot_zero_out_pay_after_shift_shortened(self):
        record, stale = self.changed_work_record()
        audit_count = OpsAuditLog.objects.count()
        with patch.object(WorkRecordViewSet, "get_object", return_value=stale):
            response = self.request(WorkRecordViewSet, record.pk, {"break_minutes": 150})
        self.assertEqual(response.status_code, 400, response.data)
        record.refresh_from_db()
        self.assertEqual(record.end_time, time(11))
        self.assertEqual(record.break_minutes, 0)
        self.assertEqual(record.work_hours, Decimal("2.00"))
        self.assertEqual(record.amount, 30000)
        self.assertEqual(OpsAuditLog.objects.count(), audit_count)

    def test_valid_stale_break_edit_uses_latest_shift_and_persists(self):
        record, stale = self.changed_work_record()
        with patch.object(WorkRecordViewSet, "get_object", return_value=stale):
            response = self.request(WorkRecordViewSet, record.pk, {"break_minutes": 30})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["amount"], 22500)
        record.refresh_from_db()
        self.assertEqual(record.end_time, time(11))
        self.assertEqual(record.work_hours, Decimal("1.50"))
        self.assertEqual(record.amount, 22500)

    def test_expense_review_audit_failure_rolls_back_and_retry_succeeds(self):
        for status in ("APPROVED", "REJECTED"):
            with self.subTest(status=status):
                expense = ExpenseRecord.objects.create(
                    tenant=self.tenant, staff=self.staff, date=date(2026, 10, 1),
                    title="QA reimbursement", amount=30000,
                )
                with patch("apps.core.services.ops_audit.record_audit", return_value=None):
                    response = self.request(ExpenseRecordViewSet, expense.pk, {"status": status})
                self.assertEqual(response.status_code, 503, response.data)
                expense.refresh_from_db()
                self.assertEqual(expense.status, "PENDING")
                self.assertIsNone(expense.approved_at)
                self.assertIsNone(expense.approved_by_id)

                response = self.request(ExpenseRecordViewSet, expense.pk, {"status": status})
                self.assertEqual(response.status_code, 200, response.data)
                expense.refresh_from_db()
                self.assertEqual(expense.status, status)
                self.assertEqual(expense.approved_by_id, self.owner.pk)
                self.assertEqual(OpsAuditLog.objects.filter(
                    action="staff.expense_reviewed", payload__expense_id=expense.pk,
                ).count(), 1)

    def test_record_deleted_after_detail_read_returns_not_found_without_recreation(self):
        record, stale = self.changed_work_record()
        record.delete()
        with patch.object(WorkRecordViewSet, "get_object", return_value=stale):
            response = self.request(WorkRecordViewSet, stale.pk, {"break_minutes": 30})
        self.assertEqual(response.status_code, 404, response.data)
        self.assertFalse(WorkRecord.objects.filter(pk=stale.pk).exists())
